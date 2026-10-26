from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounting.reports import balance_of, trial_balance
from approvals import services as ap
from approvals.models import ApprovalLevel, ApprovalWorkflow
from company.models import Company
from core.errors import ERPError
from hr import services as hr
from hr.models import EmployeeLoan, Shift
from tenants.models import Store

from . import services as pay
from .models import PayrollRun


def D(x):
    return Decimal(str(x))


@override_settings(HR_WEEKLY_OFF_DAYS=(6,))
class PayrollTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        Store.objects.create(name="Main", company=self.company)
        self.shift = Shift.objects.create(company=self.company, name="Day", start_time=time(9), end_time=time(18), standard_hours=8, half_day_hours=4)
        self.user = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-x")
        self.emp = hr.create_employee(self.company, "Ali Khan", date(2026, 1, 1), shift=self.shift, bank_name="HBL", bank_account_no="123")
        pay.set_salary(self.emp, basic=30000, house_rent=10000, tax_percent=5, overtime_multiplier=D("1.5"))

    def fill_september(self):
        for day in range(1, 31):
            d = date(2026, 9, day)
            if d.weekday() == 6:
                continue
            if day in (1, 2, 3):
                hr.record_attendance(self.emp, d, status="absent")
            elif day == 8:
                hr.record_attendance(self.emp, d, check_in=time(9), check_out=time(21))      # 12h -> 4h overtime
            else:
                hr.record_attendance(self.emp, d, check_in=time(9), check_out=time(17, 0))

    def test_calculation_numbers(self):
        self.fill_september()
        loan = hr.submit_loan(hr.create_loan(self.emp, 9000, 3, user=self.user), self.user)
        run = pay.calculate_payroll(self.company, 2026, 9, user=self.user)
        line = run.lines.get()
        self.assertEqual(line.payable_days, D(27))
        self.assertEqual(line.absent_deduction, D("4000.00"))
        self.assertEqual(line.overtime_amount, D("750.00"))      # 4h x (30000/240) x 1.5
        self.assertEqual(line.gross, D("36750.00"))
        self.assertEqual(line.tax, D("1837.50"))
        self.assertEqual(line.loan_deduction, D("3000.00"))
        self.assertEqual(line.net, D("31912.50"))
        self.assertEqual((run.total_gross, run.total_net), (D("36750.00"), D("31912.50")))

    def test_post_pay_and_journals_balance(self):
        self.fill_september()
        loan = hr.submit_loan(hr.create_loan(self.emp, 9000, 3, user=self.user), self.user)
        run = pay.calculate_payroll(self.company, 2026, 9, user=self.user)
        run = pay.submit_payroll(run, self.user)                 # no workflow -> auto-approved and posted
        self.assertEqual(run.status, PayrollRun.APPROVED)
        self.assertEqual(balance_of(self.company, "salary_expense"), D("36750.00"))
        self.assertEqual(balance_of(self.company, "salary_payable"), D("31912.50"))
        self.assertEqual(balance_of(self.company, "payroll_tax_payable"), D("1837.50"))
        self.assertEqual(balance_of(self.company, "employee_loans"), D("6000.00"))   # 9000 lent - 3000 recovered
        loan.refresh_from_db()
        self.assertEqual(loan.balance, D("6000.00"))
        pay.pay_payroll(run, self.user, method="cash")
        self.assertEqual(balance_of(self.company, "salary_payable"), D(0))
        self.assertTrue(trial_balance(self.company)["balanced"])
        with self.assertRaises(ERPError):
            pay.pay_payroll(run, self.user, method="cash")       # no double payment

    def test_recalculate_draft_but_not_after_approval_and_no_duplicates(self):
        self.fill_september()
        run1 = pay.calculate_payroll(self.company, 2026, 9)
        run2 = pay.calculate_payroll(self.company, 2026, 9)
        self.assertEqual(run1.pk, run2.pk)
        self.assertEqual(PayrollRun.objects.count(), 1)
        pay.submit_payroll(run2, self.user)
        with self.assertRaises(ERPError):
            pay.calculate_payroll(self.company, 2026, 9)

    def test_approval_required_when_workflow_exists(self):
        self.fill_september()
        cfo = get_user_model().objects.create_user("cfo", "c@example.com", "pw-12345-x")
        wf = ApprovalWorkflow.objects.create(company=self.company, name="Payroll", document_type="payroll", min_amount=0)
        ApprovalLevel.objects.create(workflow=wf, level=1, approver_user=cfo)
        run = pay.submit_payroll(pay.calculate_payroll(self.company, 2026, 9), self.user)
        self.assertEqual(run.status, "pending_approval")
        self.assertEqual(balance_of(self.company, "salary_expense"), D(0))
        with self.assertRaises(ERPError):
            pay.pay_payroll(run, self.user, method="cash")
        ap.approve(ap.get_request(run, "payroll"), cfo)
        run.refresh_from_db()
        self.assertEqual(run.status, "approved")
        self.assertEqual(balance_of(self.company, "salary_expense"), D("36750.00"))

    def test_loan_never_pushes_net_below_zero(self):
        self.fill_september()
        hr.submit_loan(hr.create_loan(self.emp, 900000, 1, user=self.user), self.user)
        line = pay.calculate_payroll(self.company, 2026, 9).lines.get()
        self.assertEqual(line.net, D(0))
        self.assertEqual(line.loan_deduction, line.gross - line.tax)

    def test_employee_without_salary_skipped_and_empty_run_rejected(self):
        other = hr.create_employee(self.company, "No Salary", date(2026, 1, 1))
        self.fill_september()
        run = pay.calculate_payroll(self.company, 2026, 9)
        self.assertIn(other.employee_code, run.note)
        self.assertEqual(run.lines.count(), 1)
        with self.assertRaises(ERPError):
            pay.calculate_payroll(Company.objects.create(name="Empty"), 2026, 9)

    def test_outputs(self):
        self.fill_september()
        run = pay.calculate_payroll(self.company, 2026, 9)
        csv_text = pay.bank_transfer_csv(run)
        self.assertIn("Ali Khan", csv_text)
        self.assertIn("HBL", csv_text)
        html = pay.payslip_html(run.lines.get())
        self.assertIn("Ali Khan", html)
        self.assertIn("NET PAY", html)
        self.assertEqual(pay.payroll_summary(run)["employees"], 1)

    def test_loan_changed_before_approval_blocks_posting(self):
        self.fill_september()
        loan = hr.submit_loan(hr.create_loan(self.emp, 9000, 3, user=self.user), self.user)
        cfo = get_user_model().objects.create_user("cfo", "c@example.com", "pw-12345-x")
        wf = ApprovalWorkflow.objects.create(company=self.company, name="Payroll", document_type="payroll", min_amount=0)
        ApprovalLevel.objects.create(workflow=wf, level=1, approver_user=cfo)
        run = pay.submit_payroll(pay.calculate_payroll(self.company, 2026, 9), self.user)
        hr.record_repayment(loan, 9000, date.today(), "cash repay")          # loan fully settled meanwhile
        with self.assertRaises(ERPError):
            ap.approve(ap.get_request(run, "payroll"), cfo)
