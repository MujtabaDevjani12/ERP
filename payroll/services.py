"""
Payroll:  calculate (draft) -> submit/approve (posts the accrual) -> pay.

  earned fixed pay = (basic + allowances) x payable_days / calendar_days
  overtime pay     = overtime_hours x (basic / (calendar_days x standard_hours)) x multiplier
  gross            = earned fixed + overtime
  net              = gross - tax - loan instalments

Accrual journal (on approval):  Dr Salary expense (gross)
                                Cr Payroll tax payable (tax), Cr Employee loans (recoveries), Cr Salary payable (net)
Payment journal:                Dr Salary payable   Cr Cash / Bank
"""
import calendar
import csv
import io
from datetime import date
from decimal import Decimal
from html import escape

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounting.services import create_journal_entry
from approvals.services import submit_for_approval
from audit.models import AuditLog
from audit.services import log_action
from core.errors import ERPError
from core.money import q2
from hr.models import Employee
from hr.services import loan_deduction_due, monthly_summary, record_repayment
from numbering.services import next_reference
from rbac.permissions import require
from .models import PayrollLine, PayrollLoanDeduction, PayrollRun, SalaryStructure

ZERO = Decimal("0")


def _u(user):
    return user if (user is not None and getattr(user, "is_authenticated", False)) else None


def set_salary(employee, *, basic, user=None, **fields):
    if q2(basic) < 0:
        raise ERPError("Basic salary cannot be negative.")
    obj, _ = SalaryStructure.objects.update_or_create(employee=employee, defaults={"basic": q2(basic), **fields})
    log_action(AuditLog.UPDATED, obj, user=user, note="salary structure set", document_number=employee.employee_code, company=employee.company)
    return obj


def _line_for(run, employee, salary):
    s = monthly_summary(employee, run.year, run.month)
    cal = s["calendar_days"]
    fixed = salary.fixed_total
    earned = q2(fixed * s["payable_days"] / cal) if cal else ZERO
    earned = min(earned, fixed)
    standard_hours = Decimal(employee.shift.standard_hours) if employee.shift_id else Decimal(8)
    hourly = salary.basic / (Decimal(cal) * standard_hours) if cal and standard_hours else ZERO
    overtime = q2(s["overtime_hours"] * hourly * salary.overtime_multiplier)
    gross = q2(earned + overtime)
    tax = q2(gross * salary.tax_percent / 100)
    available = gross - tax
    loans, loan_total = [], ZERO
    for loan, amount in loan_deduction_due(employee):
        amount = min(amount, available - loan_total)      # never push net below zero; remainder waits for next month
        if amount > 0:
            loans.append((loan, q2(amount)))
            loan_total += q2(amount)
    net = q2(gross - tax - loan_total)
    return {"employee": employee, "basic": salary.basic, "allowances": q2(fixed - salary.basic), "payable_days": s["payable_days"],
            "unpaid_days": s["unpaid_days"], "calendar_days": cal, "absent_deduction": q2(fixed - earned),
            "overtime_hours": s["overtime_hours"], "overtime_amount": overtime, "gross": gross, "tax": tax,
            "loan_deduction": q2(loan_total), "net": net}, loans


@transaction.atomic
def calculate_payroll(company, year, month, *, user=None):
    """Creates the month's draft run, or recalculates the existing draft. Employees without a salary structure are skipped
    and reported in `run.note`."""
    if not 1 <= month <= 12:
        raise ERPError("Month must be 1-12.")
    first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    run = PayrollRun.objects.select_for_update().filter(company=company, year=year, month=month).exclude(status__in=["cancelled", "rejected"]).first()
    if run is None:
        run = PayrollRun.objects.create(company=company, reference=next_reference(company, "payroll", on_date=last), year=year, month=month,
                                        created_by=_u(user))
    elif run.status != PayrollRun.DRAFT:
        raise ERPError(f"Payroll for {year}-{month:02d} is already {run.get_status_display().lower()}.")
    run.lines.all().delete()
    employees = Employee.objects.filter(company=company, joining_date__lte=last).filter(
        Q(leaving_date__isnull=True) | Q(leaving_date__gte=first)).exclude(status=Employee.INACTIVE).select_related("shift")
    skipped = []
    totals = {"gross": ZERO, "tax": ZERO, "loan": ZERO, "net": ZERO}
    for emp in employees:
        salary = getattr(emp, "salary", None)
        if salary is None:
            skipped.append(emp.employee_code)
            continue
        data, loans = _line_for(run, emp, salary)
        line = PayrollLine.objects.create(run=run, **data)
        for loan, amount in loans:
            PayrollLoanDeduction.objects.create(line=line, loan=loan, amount=amount)
        totals["gross"] += line.gross
        totals["tax"] += line.tax
        totals["loan"] += line.loan_deduction
        totals["net"] += line.net
    if not run.lines.exists():
        raise ERPError("No employees with a salary structure for this month.")
    run.total_gross, run.total_tax, run.total_loan, run.total_net = (q2(totals[k]) for k in ("gross", "tax", "loan", "net"))
    run.note = ("No salary structure: " + ", ".join(skipped))[:255] if skipped else ""
    run.save()
    log_action(AuditLog.OTHER, run, user=user, note="calculated", document_number=run.reference, company=company)
    return run


@transaction.atomic
def submit_payroll(run, user=None):
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status not in (PayrollRun.DRAFT, PayrollRun.REJECTED):
        raise ERPError("Only a draft payroll can be submitted.")
    if not run.lines.exists():
        raise ERPError("Calculate the payroll first.")
    submit_for_approval(run, "payroll", company=run.company, user=user, amount=run.total_net, number=run.reference)
    run.refresh_from_db()
    return run


@transaction.atomic
def post_payroll(run, user=None):
    """Called when the approval completes."""
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status in (PayrollRun.APPROVED, PayrollRun.PAID):
        return run
    if run.status not in (PayrollRun.PENDING, PayrollRun.DRAFT):
        raise ERPError("This payroll cannot be posted in its current status.")
    last = date(run.year, run.month, calendar.monthrange(run.year, run.month)[1])
    for line in run.lines.prefetch_related("loan_deductions__loan"):
        for d in line.loan_deductions.all():
            try:
                record_repayment(d.loan, d.amount, last, source=run.reference)
            except ERPError as exc:
                raise ERPError(f"{line.employee}: loan {d.loan.reference} changed since calculation ({exc}). Recalculate the payroll.")
    lines = [{"account": "salary_expense", "debit": run.total_gross},
             {"account": "salary_payable", "credit": run.total_net}]
    if run.total_tax > 0:
        lines.append({"account": "payroll_tax_payable", "credit": run.total_tax})
    if run.total_loan > 0:
        lines.append({"account": "employee_loans", "credit": run.total_loan})
    create_journal_entry(run.company, date=last, narration=f"Payroll {run.reference} {run.year}-{run.month:02d}",
                         source=("payroll", run.pk), user=user, lines=lines)
    run.status = PayrollRun.APPROVED
    run.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.POSTED, run, user=user, document_number=run.reference, company=run.company)
    return run


@transaction.atomic
def pay_payroll(run, user=None, *, method="bank", bank_account=None, on_date=None):
    require(user, "pay_document")
    run = PayrollRun.objects.select_for_update().get(pk=run.pk)
    if run.status == PayrollRun.PAID:
        raise ERPError("This payroll is already paid.")
    if run.status != PayrollRun.APPROVED:
        raise ERPError("Only an approved payroll can be paid.")
    on_date = on_date or timezone.localdate()
    source = "cash" if method == "cash" else (bank_account.ledger_account if bank_account is not None else "bank")
    if run.total_net > 0:
        create_journal_entry(run.company, voucher_type="PV", date=on_date, narration=f"Salary payment {run.reference}",
                             source=("payroll_payment", run.pk), user=user,
                             lines=[{"account": "salary_payable", "debit": run.total_net}, {"account": source, "credit": run.total_net}])
    run.status, run.paid_on = PayrollRun.PAID, on_date
    run.save(update_fields=["status", "paid_on", "updated_at"])
    log_action(AuditLog.OTHER, run, user=user, note="salaries paid", document_number=run.reference, company=run.company)
    return run


# ------------------------------------------------------------------ outputs
_PAGE_HEAD = """<!doctype html><html><head><meta charset="utf-8"><title>Payslips</title>
<style>body{font-family:Arial,sans-serif;max-width:640px;margin:24px auto;color:#111}table{width:100%;border-collapse:collapse}
h1{font-size:20px;margin:0}.box{border:1px solid #bbb;padding:12px;margin-top:12px}td{border-bottom:1px solid #eee}
.slip{page-break-after:always}</style></head><body>"""


def payslip_html(line):
    """A printable, self-contained payslip page (browser print -> PDF)."""
    return _PAGE_HEAD + '<div class="slip">' + _payslip_body(line) + "</div></body></html>"


def payslips_html(run):
    """Every payslip of a run in one printable page (one per sheet)."""
    slips = "".join('<div class="slip">' + _payslip_body(l) + "</div>" for l in run.lines.select_related("employee__department", "employee__designation", "run__company"))
    return _PAGE_HEAD + slips + "</body></html>"


def _payslip_body(line):
    run, emp = line.run, line.employee
    c = escape(run.company.name)

    def row(label, value, bold=False):
        w = "font-weight:700;" if bold else ""
        return f'<tr><td style="padding:4px 8px;{w}">{escape(label)}</td><td style="padding:4px 8px;text-align:right;{w}">{value:,.2f}</td></tr>'

    return f"""<h1>{c}</h1><div>Payslip for {calendar.month_name[run.month]} {run.year} &middot; {escape(run.reference)}</div>
<div class="box"><b>{escape(emp.full_name)}</b> ({escape(emp.employee_code)})<br>
{escape(emp.department.name if emp.department_id else '')} {escape(emp.designation.name if emp.designation_id else '')}<br>
Payable days: {line.payable_days} of {line.calendar_days} &middot; Unpaid days: {line.unpaid_days} &middot; Overtime hours: {line.overtime_hours}</div>
<div class="box"><table>
{row('Basic salary', line.basic)}{row('Allowances', line.allowances)}{row('Less: unpaid / pro-rata days', -line.absent_deduction)}
{row('Overtime', line.overtime_amount)}{row('Gross pay', line.gross, True)}
{row('Tax withheld', -line.tax)}{row('Loan instalment', -line.loan_deduction)}{row('NET PAY', line.net, True)}
</table></div>"""


def bank_transfer_csv(run):
    """CSV for the bank's bulk-payment upload. Employees without bank details are listed with a blank account."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["employee_code", "name", "bank", "account_no", "amount", "reference"])
    for l in run.lines.select_related("employee").order_by("employee__employee_code"):
        if l.net > 0:
            w.writerow([l.employee.employee_code, l.employee.full_name, l.employee.bank_name, l.employee.bank_account_no, f"{l.net:.2f}", run.reference])
    return out.getvalue()


def payroll_summary(run):
    return {"employees": run.lines.count(), "gross": run.total_gross, "tax": run.total_tax, "loan": run.total_loan, "net": run.total_net,
            "overtime": q2(sum((l.overtime_amount for l in run.lines.all()), ZERO)),
            "absent_deductions": q2(sum((l.absent_deduction for l in run.lines.all()), ZERO))}
