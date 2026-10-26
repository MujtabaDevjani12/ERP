"""HR services: employees, attendance (incl. late / half-day / overtime rules), leave with balances and approvals, loans."""
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F

from accounting.services import create_journal_entry
from audit.models import AuditLog
from audit.services import log_action
from core.errors import ERPError
from core.money import q2
from numbering.services import next_reference
from approvals.services import submit_for_approval
from .models import (Applicant, Attendance, Employee, EmployeeDocument, EmployeeLoan, EmployeeMovement, Holiday, LeaveBalance,
                     LeaveRequest, LeaveType, LoanRepayment, PerformanceReview)

ZERO = Decimal("0")


def _u(user):
    return user if (user is not None and getattr(user, "is_authenticated", False)) else None


def weekly_off_days():
    """Python weekday numbers (Mon=0 ... Sun=6) that are the weekly off. Configure HR_WEEKLY_OFF_DAYS in settings."""
    return tuple(getattr(settings, "HR_WEEKLY_OFF_DAYS", (6,)))


# ================================================================== employees
@transaction.atomic
def create_employee(company, full_name, joining_date, *, user=None, **fields):
    if not (full_name or "").strip():
        raise ERPError("Employee name is required.")
    code = fields.pop("employee_code", None) or next_reference(company, "employee")
    emp = Employee.objects.create(company=company, employee_code=code, full_name=full_name.strip(), joining_date=joining_date, **fields)
    log_action(AuditLog.CREATED, emp, user=user, document_number=emp.employee_code, company=company)
    return emp


@transaction.atomic
def terminate_employee(employee, leaving_date, user=None, reason=""):
    emp = Employee.objects.select_for_update().get(pk=employee.pk)
    if emp.loans.filter(status=EmployeeLoan.ACTIVE).exists():
        raise ERPError("The employee still has an active loan; settle it first.")
    emp.status, emp.leaving_date = Employee.TERMINATED, leaving_date
    emp.save(update_fields=["status", "leaving_date", "updated_at"])
    log_action(AuditLog.OTHER, emp, user=user, note=f"terminated: {reason}"[:200], document_number=emp.employee_code, company=emp.company)
    return emp


# ================================================================== attendance
OVERTIME_MIN_HOURS = Decimal("0.25")


def _minutes(t):
    return t.hour * 60 + t.minute


@transaction.atomic
def record_attendance(employee, day, *, check_in=None, check_out=None, status=None, source="manual", remarks=""):
    """Create/update one day. With times, status/late/overtime come from the employee's shift; with only a status
    (e.g. absent, leave) that status is used."""
    if employee.status != Employee.ACTIVE:
        raise ERPError(f"{employee} is not active.")
    if day < employee.joining_date:
        raise ERPError("Attendance cannot be before the joining date.")
    shift = employee.shift
    overnight = bool(shift and shift.end_time <= shift.start_time)               # e.g. 22:00 -> 06:00: check-out is the next morning
    if check_in and check_out and check_out <= check_in and not overnight:
        raise ERPError("Check-out must be after check-in (for a night shift, give the employee a shift that ends after midnight).")
    worked = ZERO
    late = 0
    overtime = ZERO
    if check_in and check_out:
        minutes = _minutes(check_out) - _minutes(check_in)
        if minutes <= 0:
            minutes += 24 * 60
        worked = q2(Decimal(minutes) / 60)
    if check_in and shift:
        late = max(_minutes(check_in) - _minutes(shift.start_time) - shift.grace_minutes, 0)
    if shift and worked - shift.standard_hours >= OVERTIME_MIN_HOURS:               # a few minutes past the end of the shift is not overtime
        overtime = q2(worked - shift.standard_hours)
    if status is None:
        if not (check_in and check_out):
            raise ERPError("Give check-in and check-out times, or a status.")
        if shift and worked < shift.half_day_hours:
            status = Attendance.ABSENT
        elif shift and worked < shift.standard_hours:
            status = Attendance.HALF_DAY
        elif late > 0:
            status = Attendance.LATE
        else:
            status = Attendance.PRESENT
    obj, _ = Attendance.objects.update_or_create(
        employee=employee, date=day,
        defaults={"check_in": check_in, "check_out": check_out, "status": status, "worked_hours": worked,
                  "late_minutes": late, "overtime_hours": overtime, "source": source, "remarks": remarks[:120]})
    return obj


def parse_time(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if hasattr(value, "hour"):
        return value.replace(second=0, microsecond=0)
    text = str(value).strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"'{value}' is not a time (use HH:MM)")


def monthly_summary(employee, year, month, *, as_of=None):
    """Counts for payroll. Days with no record in the past are absences (except weekly off and holidays)."""
    last = calendar.monthrange(year, month)[1]
    as_of = min(as_of or date(year, month, last), date(year, month, last))
    start = max(date(year, month, 1), employee.joining_date)
    end = min(as_of, employee.leaving_date) if employee.leaving_date else as_of
    records = {a.date: a for a in Attendance.objects.filter(employee=employee, date__range=(date(year, month, 1), date(year, month, last)))}
    holidays = set(Holiday.objects.filter(company=employee.company, date__range=(date(year, month, 1), date(year, month, last))).values_list("date", flat=True))
    off = weekly_off_days()
    s = {"present": 0, "late": 0, "half_day": 0, "leave_paid": 0, "leave_unpaid": 0, "absent": 0, "holidays": 0,
         "weekly_off": 0, "overtime_hours": ZERO, "late_minutes": 0, "calendar_days": last}
    day = start
    while day <= end:
        rec = records.get(day)
        if rec is not None and rec.status not in (Attendance.HOLIDAY, Attendance.WEEKLY_OFF):
            if rec.status == Attendance.PRESENT:
                s["present"] += 1
            elif rec.status == Attendance.LATE:
                s["late"] += 1
            elif rec.status == Attendance.HALF_DAY:
                s["half_day"] += 1
            elif rec.status == Attendance.LEAVE:
                s["leave_unpaid" if rec.remarks == "unpaid" else "leave_paid"] += 1
            else:
                s["absent"] += 1
            s["overtime_hours"] += rec.overtime_hours
            s["late_minutes"] += rec.late_minutes
        elif day in holidays or (rec is not None and rec.status == Attendance.HOLIDAY):
            s["holidays"] += 1
        elif day.weekday() in off or (rec is not None and rec.status == Attendance.WEEKLY_OFF):
            s["weekly_off"] += 1
        else:
            s["absent"] += 1
        day += timedelta(days=1)
    payable = s["present"] + s["late"] + s["leave_paid"] + s["holidays"] + s["weekly_off"] + Decimal("0.5") * s["half_day"]
    s["payable_days"] = Decimal(payable)
    # days before joining / after leaving are neither paid nor absent; payroll prorates by payable days
    s["counted_days"] = Decimal((end - start).days + 1) if end >= start else ZERO
    s["unpaid_days"] = s["counted_days"] - s["payable_days"]
    s["overtime_hours"] = q2(s["overtime_hours"])
    return s


# ================================================================== leave
@transaction.atomic
def ensure_leave_balances(employee, year):
    out = []
    for lt in LeaveType.objects.filter(company=employee.company):
        bal, _ = LeaveBalance.objects.get_or_create(employee=employee, leave_type=lt, year=year, defaults={"allocated": lt.days_per_year})
        out.append(bal)
    return out


def _leave_days(company, start, end):
    off, holidays = weekly_off_days(), set(Holiday.objects.filter(company=company, date__range=(start, end)).values_list("date", flat=True))
    n, day = 0, start
    while day <= end:
        if day.weekday() not in off and day not in holidays:
            n += 1
        day += timedelta(days=1)
    return Decimal(n)


@transaction.atomic
def request_leave(employee, leave_type, from_date, to_date, *, user=None, reason=""):
    if to_date < from_date:
        raise ERPError("The end date is before the start date.")
    if from_date.year != to_date.year:
        raise ERPError("A leave request cannot span two years; split it.")
    if employee.status != Employee.ACTIVE:
        raise ERPError("The employee is not active.")
    if leave_type.company_id != employee.company_id:
        raise ERPError("Leave type belongs to a different company.")
    days = _leave_days(employee.company, from_date, to_date)
    if days <= 0:
        raise ERPError("The selected dates are all weekly offs / holidays; no leave is needed.")
    overlap = LeaveRequest.objects.filter(employee=employee, status__in=[LeaveRequest.PENDING, LeaveRequest.APPROVED, LeaveRequest.DRAFT],
                                          from_date__lte=to_date, to_date__gte=from_date).exists()
    if overlap:
        raise ERPError("This overlaps another leave request.")
    if leave_type.is_paid:
        bal = ensure_leave_balances(employee, from_date.year)
        mine = next(b for b in bal if b.leave_type_id == leave_type.pk)
        pending = sum((r.days for r in LeaveRequest.objects.filter(employee=employee, leave_type=leave_type, status=LeaveRequest.PENDING,
                                                                    from_date__year=from_date.year)), ZERO)
        if days > mine.remaining - pending:
            raise ERPError(f"Not enough {leave_type.name} balance: {mine.remaining - pending} day(s) left, {days} requested.")
    req = LeaveRequest.objects.create(
        company=employee.company, reference=next_reference(employee.company, "leave_request"), employee=employee,
        leave_type=leave_type, from_date=from_date, to_date=to_date, days=days, reason=reason, created_by=_u(user))
    log_action(AuditLog.CREATED, req, user=user, document_number=req.reference, company=req.company)
    return req


@transaction.atomic
def submit_leave(req, user=None):
    req = LeaveRequest.objects.select_for_update().get(pk=req.pk)
    if req.status not in (LeaveRequest.DRAFT, LeaveRequest.REJECTED):
        raise ERPError("Only a draft request can be submitted.")
    submit_for_approval(req, "leave_request", company=req.company, user=user, amount=req.days, number=req.reference)
    req.refresh_from_db()
    return req


@transaction.atomic
def apply_approved_leave(req):
    """Deduct the balance and mark each working day as leave. Called when the approval completes."""
    req = LeaveRequest.objects.select_for_update().select_related("leave_type", "employee").get(pk=req.pk)
    if req.leave_type.is_paid:
        ensure_leave_balances(req.employee, req.from_date.year)
        LeaveBalance.objects.filter(employee=req.employee, leave_type=req.leave_type, year=req.from_date.year).update(used=F("used") + req.days)
    off, holidays = weekly_off_days(), set(Holiday.objects.filter(company=req.company, date__range=(req.from_date, req.to_date)).values_list("date", flat=True))
    day = req.from_date
    while day <= req.to_date:
        if day.weekday() not in off and day not in holidays:
            Attendance.objects.update_or_create(employee=req.employee, date=day, defaults={
                "status": Attendance.LEAVE, "source": "leave", "remarks": "paid" if req.leave_type.is_paid else "unpaid",
                "check_in": None, "check_out": None, "worked_hours": ZERO, "late_minutes": 0, "overtime_hours": ZERO})
        day += timedelta(days=1)


@transaction.atomic
def cancel_leave(req, user=None):
    req = LeaveRequest.objects.select_for_update().select_related("leave_type", "employee").get(pk=req.pk)
    if req.status == LeaveRequest.CANCELLED:
        return req
    if req.status == LeaveRequest.APPROVED:
        if req.from_date <= date.today():
            raise ERPError("Leave that has already started cannot be cancelled; correct attendance instead.")
        if req.leave_type.is_paid:
            LeaveBalance.objects.filter(employee=req.employee, leave_type=req.leave_type, year=req.from_date.year).update(used=F("used") - req.days)
        Attendance.objects.filter(employee=req.employee, date__range=(req.from_date, req.to_date), status=Attendance.LEAVE, source="leave").delete()
    req.status = LeaveRequest.CANCELLED
    req.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.CANCELLED, req, user=user, document_number=req.reference, company=req.company)
    return req


# ================================================================== loans
@transaction.atomic
def create_loan(employee, amount, installments, *, user=None, purpose=""):
    amount = q2(amount)
    if amount <= 0:
        raise ERPError("Loan amount must be greater than zero.")
    if installments < 1:
        raise ERPError("Installments must be at least 1.")
    if employee.status != Employee.ACTIVE:
        raise ERPError("The employee is not active.")
    loan = EmployeeLoan.objects.create(
        company=employee.company, reference=next_reference(employee.company, "employee_loan"), employee=employee, amount=amount,
        installments=installments, installment_amount=q2(amount / installments + Decimal("0.004")), purpose=purpose, created_by=_u(user))
    log_action(AuditLog.CREATED, loan, user=user, document_number=loan.reference, company=loan.company)
    return loan


@transaction.atomic
def submit_loan(loan, user=None):
    loan = EmployeeLoan.objects.select_for_update().get(pk=loan.pk)
    if loan.status not in (EmployeeLoan.DRAFT, EmployeeLoan.REJECTED):
        raise ERPError("Only a draft loan can be submitted.")
    submit_for_approval(loan, "employee_loan", company=loan.company, user=user, amount=loan.amount, number=loan.reference)
    loan.refresh_from_db()
    return loan


@transaction.atomic
def disburse_loan(loan, user=None, *, paid_from="cash", on_date=None):
    """Approved loan is paid out: Dr Employee Loans / Cr Cash (or bank)."""
    loan = EmployeeLoan.objects.select_for_update().select_related("employee").get(pk=loan.pk)
    if loan.status == EmployeeLoan.ACTIVE:
        return loan
    if loan.status != EmployeeLoan.PENDING:
        raise ERPError("Only an approved loan can be disbursed.")
    on_date = on_date or date.today()
    create_journal_entry(loan.company, date=on_date, narration=f"Loan {loan.reference} to {loan.employee.full_name}",
                         source=("employee_loan", loan.pk), user=user,
                         lines=[{"account": "employee_loans", "debit": loan.amount, "party_type": "employee", "party_id": loan.employee_id},
                                {"account": paid_from, "credit": loan.amount}])
    loan.status, loan.balance, loan.disbursed_on = EmployeeLoan.ACTIVE, loan.amount, on_date
    loan.save(update_fields=["status", "balance", "disbursed_on", "updated_at"])
    log_action(AuditLog.POSTED, loan, user=user, document_number=loan.reference, company=loan.company)
    return loan


@transaction.atomic
def record_repayment(loan, amount, on_date, source=""):
    """Used by payroll (salary deduction) and for cash repayments."""
    loan = EmployeeLoan.objects.select_for_update().get(pk=loan.pk)
    amount = q2(amount)
    if loan.status != EmployeeLoan.ACTIVE:
        raise ERPError("The loan is not active.")
    if amount <= 0 or amount > loan.balance:
        raise ERPError(f"Repayment must be between 0.01 and the balance {loan.balance}.")
    LoanRepayment.objects.create(loan=loan, amount=amount, date=on_date, source=source[:30])
    loan.balance -= amount
    if loan.balance == 0:
        loan.status = EmployeeLoan.CLOSED
    loan.save(update_fields=["balance", "status", "updated_at"])
    return loan


def loan_deduction_due(employee):
    """What payroll should deduct this month: one installment per active loan, never more than the balance."""
    return [(l, min(l.installment_amount, l.balance)) for l in EmployeeLoan.objects.filter(employee=employee, status=EmployeeLoan.ACTIVE)]


# ================================================================== performance
@transaction.atomic
def add_review(employee, period, rating, *, reviewer=None, kpi_score=None, strengths="", improvements=""):
    if not 1 <= rating <= 5:
        raise ERPError("Rating must be between 1 and 5.")
    review, created = PerformanceReview.objects.update_or_create(
        employee=employee, period=period, defaults={"rating": rating, "kpi_score": kpi_score, "strengths": strengths,
                                                    "improvements": improvements, "reviewer": _u(reviewer)})
    return review


# ================================================================== employee file: movements, documents
@transaction.atomic
def apply_movement(movement, user=None):
    """Record a promotion / transfer / confirmation and bring the employee's current record up to date.
    Idempotent per call: it only copies what the movement states (target department / designation / confirmation date)."""
    emp = Employee.objects.select_for_update().get(pk=movement.employee_id)
    changed = []
    if movement.from_department_id is None:
        movement.from_department = emp.department
    if movement.from_designation_id is None:
        movement.from_designation = emp.designation
    if movement.to_department_id:
        emp.department = movement.to_department
        changed.append("department")
    if movement.to_designation_id:
        emp.designation = movement.to_designation
        changed.append("designation")
    if movement.movement_type == EmployeeMovement.CONFIRMATION:
        emp.confirmation_date = movement.effective_date
        if emp.employment_type == "probation":
            emp.employment_type = "permanent"
            changed.append("employment_type")
        changed.append("confirmation_date")
    if changed:
        emp.save(update_fields=changed + ["updated_at"])
        log_action(AuditLog.OTHER, emp, user=_u(user), note=f"{movement.get_movement_type_display()} effective {movement.effective_date}"[:200],
                   document_number=emp.employee_code, company=emp.company)
    if movement.old_salary is not None or movement.new_salary is not None:
        _sync_salary(emp, movement)
    return movement


def _sync_salary(emp, movement):
    """An increment with a new salary updates the basic salary in the employee's salary structure when one exists."""
    if movement.new_salary is None or movement.movement_type not in (EmployeeMovement.INCREMENT, EmployeeMovement.PROMOTION):
        return
    try:
        structure = emp.salary
    except Exception:                      # no salary structure yet - payroll owns creating it
        return
    if movement.old_salary is None:
        movement.old_salary = structure.basic
    structure.basic = movement.new_salary
    structure.save(update_fields=["basic", "updated_at"])


def expiring_documents(company, *, within_days=60, today=None):
    """Employee documents that have expired or expire within `within_days` days (active employees only)."""
    today = today or date.today()
    return EmployeeDocument.objects.filter(
        employee__company=company, employee__status=Employee.ACTIVE, expiry_date__isnull=False,
        expiry_date__lte=today + timedelta(days=within_days),
    ).select_related("employee").order_by("expiry_date")


# ================================================================== recruitment
@transaction.atomic
def hire_applicant(applicant, joining_date, *, user=None, department=None, designation=None, employment_type=None):
    """Turn an applicant into an employee (a normal Employee record with the next employee code)."""
    applicant = Applicant.objects.select_for_update().select_related("vacancy__company").get(pk=applicant.pk)
    if applicant.hired_as_id:
        raise ERPError(f"{applicant.full_name} was already hired as {applicant.hired_as}.")
    if applicant.stage == Applicant.REJECTED:
        raise ERPError("A rejected applicant cannot be hired. Move them back to another stage first.")
    vacancy = applicant.vacancy
    emp = create_employee(
        vacancy.company, applicant.full_name, joining_date, user=user, cnic=applicant.cnic, phone=applicant.phone, email=applicant.email,
        department=department or vacancy.department, designation=designation or vacancy.designation,
        employment_type=employment_type or vacancy.employment_type,
    )
    applicant.stage, applicant.hired_as = Applicant.HIRED, emp
    applicant.save(update_fields=["stage", "hired_as", "updated_at"])
    if vacancy.status == vacancy.OPEN and vacancy.applicants.filter(stage=Applicant.HIRED).count() >= vacancy.positions:
        vacancy.status = vacancy.FILLED
        vacancy.save(update_fields=["status", "updated_at"])
    return emp
