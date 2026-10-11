from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounting.reports import balance_of
from approvals import services as ap
from approvals.models import ApprovalLevel, ApprovalWorkflow
from company.models import Company
from core.errors import ERPError
from masters import importing
from tenants.models import Store

from . import services as hr
from .models import Attendance, EmployeeLoan, Holiday, LeaveBalance, LeaveType, Shift


def D(x):
    return Decimal(str(x))


@override_settings(HR_WEEKLY_OFF_DAYS=(6,))
class HrBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.shift = Shift.objects.create(company=self.company, name="Day", start_time=time(9), end_time=time(18), grace_minutes=10,
                                          standard_hours=8, half_day_hours=4)
        self.emp = hr.create_employee(self.company, "Ali Khan", date(2026, 1, 1), shift=self.shift, store=self.store)
        self.user = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-x")


class AttendanceTests(HrBase):
    def test_status_rules(self):
        d = date(2026, 9, 7)      # Monday
        self.assertEqual(hr.record_attendance(self.emp, d, check_in=time(9, 0), check_out=time(17, 30)).status, "present")
        late = hr.record_attendance(self.emp, date(2026, 9, 8), check_in=time(9, 25), check_out=time(18, 0))
        self.assertEqual((late.status, late.late_minutes), ("late", 15))
        half = hr.record_attendance(self.emp, date(2026, 9, 9), check_in=time(9, 0), check_out=time(14, 0))
        self.assertEqual(half.status, "half_day")
        short = hr.record_attendance(self.emp, date(2026, 9, 10), check_in=time(9, 0), check_out=time(11, 0))
        self.assertEqual(short.status, "absent")
        ot = hr.record_attendance(self.emp, date(2026, 9, 11), check_in=time(9, 0), check_out=time(20, 0))
        self.assertEqual(ot.overtime_hours, D("3.00"))

    def test_validation_and_upsert(self):
        with self.assertRaises(ERPError):
            hr.record_attendance(self.emp, date(2025, 12, 31), status="present")      # before joining
        with self.assertRaises(ERPError):
            hr.record_attendance(self.emp, date(2026, 9, 7), check_in=time(18), check_out=time(9))
        hr.record_attendance(self.emp, date(2026, 9, 7), status="absent")
        hr.record_attendance(self.emp, date(2026, 9, 7), check_in=time(9), check_out=time(17, 30))
        self.assertEqual(Attendance.objects.filter(employee=self.emp, date=date(2026, 9, 7)).count(), 1)

    def test_monthly_summary(self):
        # September 2026: 30 days, Sundays are weekly off (6, 13, 20, 27)
        Holiday.objects.create(company=self.company, date=date(2026, 9, 14), name="Holiday")
        for day in range(1, 31):
            d = date(2026, 9, day)
            if d.weekday() == 6 or day == 14:
                continue
            if day == 2:
                hr.record_attendance(self.emp, d, status="absent")
            elif day == 3:
                hr.record_attendance(self.emp, d, check_in=time(9), check_out=time(14))   # half day
            elif day == 4:
                continue                                                                     # no record -> absent
            else:
                hr.record_attendance(self.emp, d, check_in=time(9), check_out=time(17, 30))
        s = hr.monthly_summary(self.emp, 2026, 9)
        self.assertEqual((s["absent"], s["half_day"], s["holidays"], s["weekly_off"]), (2, 1, 1, 4))
        self.assertEqual(s["payable_days"], D(30) - 2 - D("0.5"))
        self.assertEqual(s["unpaid_days"], D("2.5"))

    def test_mid_month_joiner_not_marked_absent_before_joining(self):
        late_joiner = hr.create_employee(self.company, "New Guy", date(2026, 9, 21))
        s = hr.monthly_summary(late_joiner, 2026, 9)
        self.assertEqual(s["counted_days"], D(10))
        self.assertEqual((s["weekly_off"], s["absent"]), (1, 9))
        self.assertEqual(s["unpaid_days"], D(9))


class LeaveTests(HrBase):
    def setUp(self):
        super().setUp()
        self.annual = LeaveType.objects.create(company=self.company, name="Annual", days_per_year=10, is_paid=True)
        self.unpaid = LeaveType.objects.create(company=self.company, name="Unpaid", days_per_year=0, is_paid=False)

    def test_leave_flow_deducts_balance_and_marks_attendance(self):
        req = hr.request_leave(self.emp, self.annual, date(2030, 10, 14), date(2030, 10, 16), user=self.user)   # Mon-Wed
        self.assertEqual(req.days, D(3))
        req = hr.submit_leave(req, self.user)               # no workflow -> auto approved
        self.assertEqual(req.status, "approved")
        bal = LeaveBalance.objects.get(employee=self.emp, leave_type=self.annual, year=2030)
        self.assertEqual(bal.remaining, D(7))
        self.assertEqual(Attendance.objects.filter(employee=self.emp, status="leave").count(), 3)
        hr.cancel_leave(req, self.user)
        bal.refresh_from_db()
        self.assertEqual(bal.remaining, D(10))
        self.assertEqual(Attendance.objects.filter(employee=self.emp, status="leave").count(), 0)

    def test_weekends_skipped_overlap_and_balance_checks(self):
        req = hr.request_leave(self.emp, self.annual, date(2030, 10, 11), date(2030, 10, 14))   # Fri..Mon incl Sunday
        self.assertEqual(req.days, D(3))
        with self.assertRaises(ERPError):
            hr.request_leave(self.emp, self.annual, date(2030, 10, 14), date(2030, 10, 15))    # overlaps
        with self.assertRaises(ERPError):
            hr.request_leave(self.emp, self.annual, date(2030, 11, 4), date(2030, 11, 22))     # > balance
        ok = hr.request_leave(self.emp, self.unpaid, date(2030, 11, 4), date(2030, 11, 22))    # unpaid has no cap
        self.assertTrue(ok.days > 10)
        with self.assertRaises(ERPError):
            hr.request_leave(self.emp, self.annual, date(2030, 10, 6), date(2030, 10, 6))      # only a Sunday

    def test_leave_needs_approver_when_workflow_exists(self):
        boss = get_user_model().objects.create_user("hrm", "h@example.com", "pw-12345-x")
        wf = ApprovalWorkflow.objects.create(company=self.company, name="Leave", document_type="leave_request", min_amount=0)
        ApprovalLevel.objects.create(workflow=wf, level=1, approver_user=boss)
        req = hr.submit_leave(hr.request_leave(self.emp, self.annual, date(2030, 10, 14), date(2030, 10, 14)), self.user)
        self.assertEqual(req.status, "pending_approval")
        self.assertEqual(Attendance.objects.filter(status="leave").count(), 0)
        ap.approve(ap.get_request(req, "leave_request"), boss)
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        self.assertEqual(Attendance.objects.filter(status="leave").count(), 1)


class LoanTests(HrBase):
    def test_loan_disbursed_on_approval_and_repaid(self):
        loan = hr.create_loan(self.emp, 12000, 4, user=self.user)
        self.assertEqual(loan.installment_amount, D("3000.00"))
        loan = hr.submit_loan(loan, self.user)
        self.assertEqual(loan.status, "active")
        self.assertEqual(loan.balance, D("12000.00"))
        self.assertEqual(balance_of(self.company, "employee_loans"), D("12000.00"))
        due = hr.loan_deduction_due(self.emp)
        self.assertEqual(due[0][1], D("3000.00"))
        for _ in range(4):
            hr.record_repayment(loan, 3000, date.today(), "PAY-1")
        loan.refresh_from_db()
        self.assertEqual((loan.status, loan.balance), ("closed", D(0)))
        with self.assertRaises(ERPError):
            hr.record_repayment(loan, 1, date.today())

    def test_cannot_overpay_and_terminate_blocked_with_active_loan(self):
        loan = hr.submit_loan(hr.create_loan(self.emp, 1000, 2, user=self.user), self.user)
        with self.assertRaises(ERPError):
            hr.record_repayment(loan, 1500, date.today())
        with self.assertRaises(ERPError):
            hr.terminate_employee(self.emp, date.today())


class AttendanceImportTests(HrBase):
    def test_excel_import_all_or_nothing(self):
        from io import BytesIO
        from openpyxl import Workbook

        def book(rows):
            wb = Workbook()
            ws = wb.active
            ws.append(["employee_code", "date", "check_in", "check_out", "status"])
            for r in rows:
                ws.append(r)
            buf = BytesIO()
            wb.save(buf)
            buf.seek(0)
            return buf

        bad = importing.create_import_job(self.company, "attendance", book([
            [self.emp.employee_code, "2026-09-07", "09:00", "17:30", ""], ["NOPE", "2026-09-07", "09:00", "17:30", ""]]))
        self.assertEqual(bad.status, "failed")
        self.assertEqual(Attendance.objects.count(), 0)
        good = importing.create_import_job(self.company, "attendance", book([
            [self.emp.employee_code, "2026-09-07", "09:00", "17:30", ""], [self.emp.employee_code, "2026-09-08", "", "", "absent"]]))
        self.assertEqual(good.status, "validated", good.errors)
        importing.confirm_import(good)
        self.assertEqual(Attendance.objects.count(), 2)
