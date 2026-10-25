"""
HR, payroll and accounting reports that were missing. Same contract as registry.py:
report(company, p) -> (headers, rows), registered with the permission needed to see it.
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from core.money import q2
from .registry import ZERO, register


def _name(obj):
    return str(obj) if obj is not None else ""


# =============================================================================================== accounting
@register("day_book", "Day book (all vouchers)", "Accounting", "accounting.view_journalentry")
def day_book(company, p):
    from accounting.models import JournalLine, POSTED_STATUSES
    qs = (JournalLine.objects.filter(entry__company=company, entry__status__in=POSTED_STATUSES,
                                     entry__date__gte=p.date_from, entry__date__lte=p.date_to)
          .select_related("entry", "account").order_by("entry__date", "entry_id", "id"))
    if p.store is not None:
        qs = qs.filter(entry__store=p.store)
    rows, total_d, total_c = [], ZERO, ZERO
    for l in qs:
        rows.append([l.entry.date, l.entry.entry_number, l.entry.voucher_type, f"{l.account.code} {l.account.name}",
                     l.description or l.entry.narration, l.debit, l.credit])
        total_d += l.debit
        total_c += l.credit
    rows.append(["", "", "", "", "TOTAL", q2(total_d), q2(total_c)])
    return ["Date", "Voucher", "Type", "Account", "Narration", "Debit", "Credit"], rows


@register("general_ledger_all", "General ledger (all accounts)", "Accounting", "accounting.view_journalentry")
def general_ledger_all(company, p):
    from accounting.models import Account, JournalLine, POSTED_STATUSES
    from accounting.reports import natural_balance
    base = JournalLine.objects.filter(entry__company=company, entry__status__in=POSTED_STATUSES)
    opening = {r["account_id"]: (r["d"] or ZERO, r["c"] or ZERO) for r in
               base.filter(entry__date__lt=p.date_from).values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))}
    moves = defaultdict(list)
    for l in (base.filter(entry__date__gte=p.date_from, entry__date__lte=p.date_to).select_related("entry")
              .order_by("entry__date", "entry_id", "id")):
        moves[l.account_id].append(l)
    rows = []
    for account in Account.objects.filter(company=company, is_group=False).order_by("code"):
        if account.id not in moves and account.id not in opening:
            continue
        od, oc = opening.get(account.id, (ZERO, ZERO))
        running = natural_balance(account, od, oc)
        if account.id not in moves and running == 0:
            continue
        label = f"{account.code} {account.name}"
        rows.append([label, "", "", "Opening balance", "", "", q2(running)])
        for l in moves.get(account.id, []):
            running += natural_balance(account, l.debit, l.credit)
            rows.append([label, l.entry.date, l.entry.entry_number, l.description or l.entry.narration, l.debit, l.credit, q2(running)])
        rows.append([label, "", "", "Closing balance", "", "", q2(running)])
    return ["Account", "Date", "Voucher", "Narration", "Debit", "Credit", "Balance"], rows


@register("chart_of_accounts", "Chart of accounts with balances", "Accounting", "accounting.view_account", "as_of")
def chart_of_accounts(company, p):
    from accounting.models import Account
    from accounting.reports import account_totals, natural_balance
    totals = account_totals(company, date_to=p.as_of)
    rows = []
    for a in Account.objects.filter(company=company).select_related("parent").order_by("code"):
        d, c = totals.get(a.id, (ZERO, ZERO))
        rows.append([a.code, a.name, a.get_account_type_display(), a.parent.code if a.parent else "", "Group" if a.is_group else "Posting",
                     "Yes" if a.is_active else "No", q2(natural_balance(a, d, c)) if not a.is_group else ""])
    return ["Code", "Account", "Type", "Parent", "Kind", "Active", "Balance"], rows


@register("bank_balances", "Bank & cash balances", "Accounting", "accounting.view_bankaccount", "as_of")
def bank_balances(company, p):
    from accounting.models import BankAccount
    from accounting.reports import balance_of
    rows, total = [], ZERO
    for b in BankAccount.objects.filter(company=company).select_related("ledger_account").order_by("name"):
        bal = balance_of(company, b.ledger_account, as_of=p.as_of)
        total += bal
        rows.append([b.name, b.bank_name, b.account_number, b.ledger_account.code, "Yes" if b.is_active else "No", q2(bal)])
    rows.append(["TOTAL", "", "", "", "", q2(total)])
    return ["Account", "Bank", "Account no.", "Ledger", "Active", "Book balance"], rows


@register("fixed_asset_register", "Fixed asset register", "Accounting", "accounting.view_fixedasset", "none")
def fixed_asset_register(company, p):
    from accounting.models import FixedAsset
    rows, cost, accum = [], ZERO, ZERO
    for a in FixedAsset.objects.filter(company=company).order_by("code"):
        rows.append([a.code, a.name, a.purchase_date, a.purchase_cost, a.accumulated_depreciation, q2(a.current_value),
                     a.get_method_display(), a.location, a.get_status_display()])
        if a.status == "active":
            cost += a.purchase_cost
            accum += a.accumulated_depreciation
    rows.append(["", "TOTAL (active)", "", q2(cost), q2(accum), q2(cost - accum), "", "", ""])
    return ["Code", "Asset", "Purchased", "Cost", "Accum. depreciation", "Book value", "Method", "Location", "Status"], rows


@register("budget_vs_actual", "Budget vs actual", "Accounting", "accounting.view_budget", "none")
def budget_vs_actual(company, p):
    from accounting.budgets import actual_vs_budget
    from accounting.models import Budget
    rows = []
    for budget in Budget.objects.filter(company=company, is_active=True).order_by("-fiscal_year", "name"):
        data = actual_vs_budget(budget)
        for r in data["rows"]:
            rows.append([str(budget), r["account"], r["name"], r["month"], r["budget"], r["actual"], r["variance"]])
        if data["rows"]:
            rows.append([str(budget), "", "TOTAL", "", data["total_budget"], data["total_actual"], data["total_variance"]])
    return ["Budget", "Code", "Account", "Month", "Budget", "Actual", "Variance"], rows


@register("cheque_register", "Cheque register", "Accounting", "accounting.view_cheque")
def cheque_register(company, p):
    from accounting.models import Cheque
    qs = Cheque.objects.filter(company=company, cheque_date__gte=p.date_from, cheque_date__lte=p.date_to).select_related("bank_account")
    rows, issued, received = [], ZERO, ZERO
    for c in qs.order_by("cheque_date", "id"):
        rows.append([c.cheque_date, c.cheque_number, c.get_cheque_type_display(), c.party_name, c.bank_account.name, c.amount,
                     c.get_status_display(), c.cleared_on or ""])
        if c.status == Cheque.PENDING:
            if c.cheque_type == Cheque.ISSUED:
                issued += c.amount
            else:
                received += c.amount
    rows.append(["", "", "", "", "Pending - to pay", q2(issued), "", ""])
    rows.append(["", "", "", "", "Pending - to collect", q2(received), "", ""])
    return ["Cheque date", "Cheque no.", "Type", "Party", "Bank account", "Amount", "Status", "Cleared on"], rows


@register("recurring_journals", "Recurring journals schedule", "Accounting", "accounting.view_recurringjournal", "none")
def recurring_journals(company, p):
    from accounting.models import RecurringJournal
    rows = []
    for r in RecurringJournal.objects.filter(company=company).prefetch_related("lines").order_by("next_run_date"):
        amount = sum((l.debit for l in r.lines.all()), ZERO)
        rows.append([r.name, r.get_frequency_display(), r.next_run_date, r.end_date or "", r.last_run_date or "", amount, "Active" if r.is_active else "Stopped"])
    return ["Journal", "Repeats", "Next date", "Ends", "Last posted", "Amount", "Status"], rows


@register("fiscal_years", "Fiscal years & lock status", "Accounting", "accounting.view_fiscalyear", "none")
def fiscal_years(company, p):
    from accounting.models import FiscalYear
    rows = [[f.name, f.start_date, f.end_date, "Closed" if f.is_closed else "Open", f.closed_on.date() if f.closed_on else ""]
            for f in FiscalYear.objects.filter(company=company).order_by("start_date")]
    return ["Year", "From", "To", "Status", "Closed on"], rows


# =============================================================================================== HR
@register("employee_master", "Employee master list", "HR", "hr.view_employee", "none")
def employee_master(company, p):
    from hr.models import Employee
    rows = [[e.employee_code, e.full_name, _name(e.department), _name(e.designation), e.get_employment_type_display(), e.joining_date,
             e.phone, e.cnic, e.get_status_display()]
            for e in Employee.objects.filter(company=company).select_related("department", "designation").order_by("employee_code")]
    return ["Code", "Name", "Department", "Designation", "Type", "Joined", "Phone", "CNIC", "Status"], rows


@register("headcount", "Headcount by department", "HR", "hr.view_employee", "none")
def headcount(company, p):
    from hr.models import Employee
    data = (Employee.objects.filter(company=company).values("department__name")
            .annotate(active=Count("id", filter=Q(status="active")), inactive=Count("id", filter=Q(status="inactive")),
                      left=Count("id", filter=Q(status="terminated")), total=Count("id")).order_by("department__name"))
    rows = [[r["department__name"] or "(No department)", r["active"], r["inactive"], r["left"], r["total"]] for r in data]
    rows.append(["TOTAL", sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows), sum(r[4] for r in rows)])
    return ["Department", "Active", "Inactive", "Left", "Total"], rows


@register("joiners_leavers", "New joiners & leavers", "HR", "hr.view_employee")
def joiners_leavers(company, p):
    from hr.models import Employee
    rows = []
    for e in Employee.objects.filter(company=company, joining_date__gte=p.date_from, joining_date__lte=p.date_to).order_by("joining_date"):
        rows.append(["Joined", e.joining_date, e.employee_code, e.full_name, _name(e.department), _name(e.designation)])
    for e in Employee.objects.filter(company=company, leaving_date__gte=p.date_from, leaving_date__lte=p.date_to).order_by("leaving_date"):
        rows.append(["Left", e.leaving_date, e.employee_code, e.full_name, _name(e.department), _name(e.designation)])
    return ["Event", "Date", "Code", "Name", "Department", "Designation"], sorted(rows, key=lambda r: (r[1], r[0]))


@register("attendance_summary", "Attendance summary by employee", "HR", "hr.view_attendance")
def attendance_summary(company, p):
    from hr.models import Attendance
    data = (Attendance.objects.filter(employee__company=company, date__gte=p.date_from, date__lte=p.date_to)
            .values("employee__employee_code", "employee__full_name")
            .annotate(present=Count("id", filter=Q(status__in=["present", "late"])), late=Count("id", filter=Q(status="late")),
                      half=Count("id", filter=Q(status="half_day")), absent=Count("id", filter=Q(status="absent")),
                      leave=Count("id", filter=Q(status="leave")), late_min=Sum("late_minutes"), ot=Sum("overtime_hours"),
                      hours=Sum("worked_hours")).order_by("employee__employee_code"))
    rows = [[r["employee__employee_code"], r["employee__full_name"], r["present"], r["late"], r["half"], r["absent"], r["leave"],
             r["late_min"] or 0, q2(r["hours"] or 0), q2(r["ot"] or 0)] for r in data]
    return ["Code", "Employee", "Present", "Late", "Half day", "Absent", "Leave", "Late minutes", "Hours worked", "Overtime hours"], rows


@register("leave_balances", "Leave balances", "HR", "hr.view_leavebalance", "as_of")
def leave_balances(company, p):
    from hr.models import LeaveBalance
    qs = (LeaveBalance.objects.filter(employee__company=company, year=p.as_of.year, employee__status="active")
          .select_related("employee", "leave_type").order_by("employee__employee_code", "leave_type__name"))
    return ["Code", "Employee", "Leave type", "Allocated", "Used", "Remaining"], [
        [b.employee.employee_code, b.employee.full_name, b.leave_type.name, b.allocated, b.used, b.remaining] for b in qs]


@register("leave_register", "Leave register", "HR", "hr.view_leaverequest")
def leave_register(company, p):
    from hr.models import LeaveRequest
    qs = (LeaveRequest.objects.filter(company=company, from_date__lte=p.date_to, to_date__gte=p.date_from)
          .select_related("employee", "leave_type").order_by("from_date"))
    return ["Reference", "Employee", "Leave type", "From", "To", "Days", "Status"], [
        [r.reference, r.employee.full_name, r.leave_type.name, r.from_date, r.to_date, r.days, r.get_status_display()] for r in qs]


@register("loan_outstanding", "Employee loans outstanding", "HR", "hr.view_employeeloan", "none")
def loan_outstanding(company, p):
    from hr.models import EmployeeLoan
    qs = EmployeeLoan.objects.filter(company=company, status="active").select_related("employee").order_by("employee__employee_code")
    rows = [[l.reference, l.employee.employee_code, l.employee.full_name, l.disbursed_on or "", l.amount, l.installments, l.installment_amount, l.balance]
            for l in qs]
    rows.append(["", "", "TOTAL", "", sum((l.amount for l in qs), ZERO), "", "", sum((l.balance for l in qs), ZERO)])
    return ["Loan", "Code", "Employee", "Disbursed", "Amount", "Installments", "Per month", "Balance"], rows


@register("document_expiry", "Employee documents expiring (next 60 days)", "HR", "hr.view_employeedocument", "none")
def document_expiry(company, p):
    from hr.services import expiring_documents
    rows = [[d.expiry_date, d.employee.employee_code, d.employee.full_name, d.get_doc_type_display(), d.title]
            for d in expiring_documents(company, within_days=60)]
    return ["Expires", "Code", "Employee", "Type", "Document"], rows


@register("disciplinary_register", "Disciplinary register", "HR", "hr.view_disciplinaryaction")
def disciplinary_register(company, p):
    from hr.models import DisciplinaryAction
    qs = (DisciplinaryAction.objects.filter(employee__company=company, date__gte=p.date_from, date__lte=p.date_to)
          .select_related("employee").order_by("date"))
    return ["Date", "Code", "Employee", "Action", "Reason", "Outcome", "Status"], [
        [d.date, d.employee.employee_code, d.employee.full_name, d.get_action_type_display(), d.reason, d.outcome, d.get_status_display()] for d in qs]


@register("movements", "Promotions, transfers & increments", "HR", "hr.view_employeemovement")
def movements(company, p):
    from hr.models import EmployeeMovement
    qs = (EmployeeMovement.objects.filter(employee__company=company, effective_date__gte=p.date_from, effective_date__lte=p.date_to)
          .select_related("employee", "from_designation", "to_designation", "from_department", "to_department").order_by("effective_date"))
    return ["Effective", "Code", "Employee", "Type", "From", "To", "Old salary", "New salary"], [
        [m.effective_date, m.employee.employee_code, m.employee.full_name, m.get_movement_type_display(),
         " / ".join(x for x in (_name(m.from_department), _name(m.from_designation)) if x),
         " / ".join(x for x in (_name(m.to_department), _name(m.to_designation)) if x), m.old_salary or "", m.new_salary or ""] for m in qs]


@register("recruitment_pipeline", "Recruitment pipeline", "HR", "hr.view_vacancy", "none")
def recruitment_pipeline(company, p):
    from hr.models import Vacancy
    data = Vacancy.objects.filter(company=company).annotate(
        n=Count("applicants"), applied=Count("applicants", filter=Q(applicants__stage="applied")),
        screening=Count("applicants", filter=Q(applicants__stage="screening")), interview=Count("applicants", filter=Q(applicants__stage="interview")),
        offered=Count("applicants", filter=Q(applicants__stage="offered")), hired=Count("applicants", filter=Q(applicants__stage="hired")),
        rejected=Count("applicants", filter=Q(applicants__stage="rejected"))).order_by("status", "-created_at")
    return ["Vacancy", "Department", "Positions", "Status", "Closing", "Applicants", "Applied", "Screening", "Interview", "Offered", "Hired", "Rejected"], [
        [v.title, _name(v.department), v.positions, v.get_status_display(), v.closing_date or "", v.n, v.applied, v.screening, v.interview,
         v.offered, v.hired, v.rejected] for v in data]


@register("training_summary", "Training programs", "HR", "hr.view_trainingprogram")
def training_summary(company, p):
    from hr.models import TrainingProgram
    data = TrainingProgram.objects.filter(company=company, start_date__lte=p.date_to).filter(
        Q(end_date__gte=p.date_from) | Q(end_date__isnull=True, start_date__gte=p.date_from)).annotate(
        n=Count("participants"), attended=Count("participants", filter=Q(participants__attended=True))).order_by("start_date")
    return ["Program", "Trainer", "From", "To", "Status", "Participants", "Attended", "Cost"], [
        [t.title, t.trainer, t.start_date, t.end_date or "", t.get_status_display(), t.n, t.attended, t.cost] for t in data]


# =============================================================================================== payroll
@register("payroll_summary", "Payroll runs summary", "Payroll", "payroll.view_payrollrun")
def payroll_summary(company, p):
    from payroll.models import PayrollRun
    qs = PayrollRun.objects.filter(company=company).exclude(status__in=["cancelled", "rejected"]).order_by("year", "month")
    runs = [r for r in qs if date_in_range(r.year, r.month, p.date_from, p.date_to)]
    rows = [[r.reference, f"{r.year}-{r.month:02d}", r.get_status_display(), r.total_gross, r.total_tax, r.total_loan, r.total_net, r.paid_on or ""] for r in runs]
    rows.append(["TOTAL", "", "", sum((r.total_gross for r in runs), ZERO), sum((r.total_tax for r in runs), ZERO),
                 sum((r.total_loan for r in runs), ZERO), sum((r.total_net for r in runs), ZERO), ""])
    return ["Run", "Month", "Status", "Gross", "Tax", "Loan recovery", "Net pay", "Paid on"], rows


def date_in_range(year, month, date_from, date_to):
    from datetime import date
    first = date(year, month, 1)
    return date(date_from.year, date_from.month, 1) <= first <= date_to


@register("payroll_register", "Payroll register (by employee)", "Payroll", "payroll.view_payrollrun")
def payroll_register(company, p):
    from payroll.models import PayrollLine
    qs = (PayrollLine.objects.filter(run__company=company).exclude(run__status__in=["cancelled", "rejected", "draft"])
          .select_related("run", "employee").order_by("run__year", "run__month", "employee__employee_code"))
    rows = []
    for l in qs:
        if not date_in_range(l.run.year, l.run.month, p.date_from, p.date_to):
            continue
        rows.append([f"{l.run.year}-{l.run.month:02d}", l.employee.employee_code, l.employee.full_name, l.basic, l.allowances,
                     l.absent_deduction, l.overtime_amount, l.gross, l.tax, l.loan_deduction, l.net])
    totals = [sum((r[i] for r in rows), ZERO) for i in range(3, 11)]
    rows.append(["TOTAL", "", ""] + totals)
    return ["Month", "Code", "Employee", "Basic", "Allowances", "Absence ded.", "Overtime", "Gross", "Tax", "Loan", "Net"], rows


@register("salary_by_department", "Payroll cost by department", "Payroll", "payroll.view_payrollrun")
def salary_by_department(company, p):
    from payroll.models import PayrollLine
    qs = (PayrollLine.objects.filter(run__company=company).exclude(run__status__in=["cancelled", "rejected", "draft"])
          .select_related("run", "employee__department"))
    agg = defaultdict(lambda: [ZERO, ZERO, ZERO, set()])
    for l in qs:
        if not date_in_range(l.run.year, l.run.month, p.date_from, p.date_to):
            continue
        a = agg[_name(l.employee.department) or "(No department)"]
        a[0] += l.gross
        a[1] += l.tax
        a[2] += l.net
        a[3].add(l.employee_id)
    rows = [[k, len(v[3]), q2(v[0]), q2(v[1]), q2(v[2])] for k, v in sorted(agg.items())]
    rows.append(["TOTAL", "", sum((r[2] for r in rows), ZERO), sum((r[3] for r in rows), ZERO), sum((r[4] for r in rows), ZERO)])
    return ["Department", "Employees", "Gross", "Tax", "Net pay"], rows


@register("salary_structures", "Salary structures", "Payroll", "payroll.view_salarystructure", "none")
def salary_structures(company, p):
    from payroll.models import SalaryStructure
    qs = SalaryStructure.objects.filter(employee__company=company, employee__status="active").select_related("employee").order_by("employee__employee_code")
    rows = [[s.employee.employee_code, s.employee.full_name, s.basic, s.house_rent, s.transport, s.medical, s.other_allowance, s.fixed_total, s.tax_percent]
            for s in qs]
    rows.append(["", "TOTAL", *[sum((r[i] for r in rows), ZERO) for i in range(2, 8)], ""])
    return ["Code", "Employee", "Basic", "House rent", "Transport", "Medical", "Other", "Gross (fixed)", "Tax %"], rows
