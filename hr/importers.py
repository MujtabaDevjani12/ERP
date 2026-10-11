"""Registers the 'attendance' Excel import with masters.importing."""
from datetime import date, datetime


def _parse_date(value):
    if hasattr(value, "date") and callable(value.date):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"'{value}' is not a date (use YYYY-MM-DD)")


def register(imp):
    from .models import Attendance, Employee
    from .services import parse_time, record_attendance

    def prepare(ctx):
        ctx["employees"] = {e.employee_code.lower(): e for e in Employee.objects.filter(company=ctx["company"]).select_related("shift")}

    def validate(row, ctx):
        errors = []
        emp = ctx["employees"].get(str(row.get("employee_code") or "").strip().lower())
        if emp is None:
            errors.append(f"employee_code '{row.get('employee_code')}' not found")
        elif emp.status != Employee.ACTIVE:
            errors.append(f"{emp.employee_code} is not active")
        try:
            day = _parse_date(row.get("date"))
        except ValueError as exc:
            errors.append(str(exc))
            day = None
        try:
            cin, cout = parse_time(row.get("check_in")), parse_time(row.get("check_out"))
        except ValueError as exc:
            errors.append(str(exc))
            cin = cout = None
        status = str(row.get("status") or "").strip().lower() or None
        if status and status not in dict(Attendance.STATUS_CHOICES):
            errors.append(f"status '{status}' must be one of {', '.join(dict(Attendance.STATUS_CHOICES))}")
        if not status and not (cin and cout):
            errors.append("give check_in and check_out, or a status")
        if cin and cout and cout <= cin:
            errors.append("check_out must be after check_in")
        if emp and day:
            if day < emp.joining_date:
                errors.append("date is before the joining date")
            key = ("att", emp.pk, day)
            if key in ctx["seen"]:
                errors.append("duplicate employee/date in this file")
            ctx["seen"].add(key)
        return {"employee_id": emp.pk if emp else None, "date": day, "check_in": cin.strftime("%H:%M") if cin else "",
                "check_out": cout.strftime("%H:%M") if cout else "", "status": status or ""}, errors

    def commit(rows, ctx):
        for r in rows:
            emp = Employee.objects.select_related("shift").get(pk=r["employee_id"])
            record_attendance(emp, date.fromisoformat(r["date"]), check_in=parse_time(r["check_in"]), check_out=parse_time(r["check_out"]),
                              status=r["status"] or None, source="import")

    imp.register_import_type("attendance", label="Attendance", required=("employee_code", "date"),
                             optional=("check_in", "check_out", "status"), validate_row=validate, commit=commit, prepare=prepare)
