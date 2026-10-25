"""Employee file, movements, recruitment, training and the new ERP screens."""
from datetime import date, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from company.models import Company
from core.errors import ERPError
from tenants.models import Store

from . import services as hr
from .models import (Applicant, Department, Designation, DisciplinaryAction, EmployeeDocument, EmployeeMovement, TrainingParticipant,
                     TrainingProgram, Vacancy)


class HrAdditionsBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.sales = Department.objects.create(company=self.company, name="Sales")
        self.ops = Department.objects.create(company=self.company, name="Operations")
        self.exec_ = Designation.objects.create(company=self.company, name="Executive")
        self.mgr = Designation.objects.create(company=self.company, name="Manager")
        self.emp = hr.create_employee(self.company, "Ali Khan", date(2026, 1, 1), store=self.store, department=self.sales,
                                      designation=self.exec_, employment_type="probation")
        self.boss = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-x")


class MovementTests(HrAdditionsBase):
    def test_promotion_updates_post(self):
        m = EmployeeMovement(employee=self.emp, movement_type="promotion", effective_date=date(2026, 7, 1), to_designation=self.mgr, to_department=self.ops)
        hr.apply_movement(m, user=self.boss)
        m.save()
        self.emp.refresh_from_db()
        self.assertEqual((self.emp.designation, self.emp.department), (self.mgr, self.ops))
        self.assertEqual((m.from_designation, m.from_department), (self.exec_, self.sales))

    def test_confirmation_makes_probation_employee_permanent(self):
        m = EmployeeMovement(employee=self.emp, movement_type="confirmation", effective_date=date(2026, 4, 1))
        hr.apply_movement(m, user=self.boss)
        self.emp.refresh_from_db()
        self.assertEqual((self.emp.employment_type, self.emp.confirmation_date), ("permanent", date(2026, 4, 1)))

    def test_increment_updates_salary_structure(self):
        from payroll.models import SalaryStructure
        SalaryStructure.objects.create(employee=self.emp, basic=Decimal("50000"))
        m = EmployeeMovement(employee=self.emp, movement_type="increment", effective_date=date(2026, 7, 1), new_salary=Decimal("60000"))
        hr.apply_movement(m, user=self.boss)
        self.assertEqual(m.old_salary, Decimal("50000.00"))
        self.assertEqual(SalaryStructure.objects.get(employee=self.emp).basic, Decimal("60000"))


class RecruitmentTests(HrAdditionsBase):
    def setUp(self):
        super().setUp()
        self.vacancy = Vacancy.objects.create(company=self.company, title="Sales Executive", department=self.sales, designation=self.exec_, positions=1)
        self.applicant = Applicant.objects.create(vacancy=self.vacancy, full_name="Sara Ahmed", phone="0300-1111111", stage="offered")

    def test_hire_creates_employee_and_fills_vacancy(self):
        emp = hr.hire_applicant(self.applicant, date(2026, 8, 1), user=self.boss)
        self.applicant.refresh_from_db()
        self.vacancy.refresh_from_db()
        self.assertEqual((emp.full_name, emp.department, emp.designation), ("Sara Ahmed", self.sales, self.exec_))
        self.assertEqual((self.applicant.stage, self.applicant.hired_as), ("hired", emp))
        self.assertEqual(self.vacancy.status, "filled")

    def test_cannot_hire_twice_or_a_rejected_applicant(self):
        hr.hire_applicant(self.applicant, date(2026, 8, 1))
        with self.assertRaises(ERPError):
            hr.hire_applicant(self.applicant, date(2026, 8, 1))
        other = Applicant.objects.create(vacancy=self.vacancy, full_name="Bilal", stage="rejected")
        with self.assertRaises(ERPError):
            hr.hire_applicant(other, date(2026, 8, 1))


class DocumentTests(HrAdditionsBase):
    def test_expiring_documents_only_for_active_employees_within_window(self):
        today = date(2026, 9, 1)
        EmployeeDocument.objects.create(employee=self.emp, title="Work permit", doc_type="license", expiry_date=today + timedelta(days=20))
        EmployeeDocument.objects.create(employee=self.emp, title="Old CNIC", doc_type="cnic", expiry_date=today - timedelta(days=5))
        EmployeeDocument.objects.create(employee=self.emp, title="Degree", doc_type="degree")                       # never expires
        EmployeeDocument.objects.create(employee=self.emp, title="Far away", doc_type="other", expiry_date=today + timedelta(days=400))
        titles = [d.title for d in hr.expiring_documents(self.company, within_days=60, today=today)]
        self.assertEqual(titles, ["Old CNIC", "Work permit"])


class HrScreensTests(HrAdditionsBase):
    """Every new model has a working list + add form in the ERP screens, and the new reports run."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)

    def test_list_and_add_screens_render(self):
        for model in ("employeedocument", "employeemovement", "disciplinaryaction", "vacancy", "applicant", "trainingprogram", "trainingparticipant", "employee"):
            self.assertEqual(self.client.get(reverse("erp_ui:list", args=["hr", model])).status_code, 200, model)
            self.assertEqual(self.client.get(reverse("erp_ui:add", args=["hr", model])).status_code, 200, model)

    def test_create_vacancy_and_training_through_the_screens(self):
        resp = self.client.post(reverse("erp_ui:add", args=["hr", "vacancy"]), {
            "company": self.company.pk, "title": "Accountant", "positions": 2, "employment_type": "permanent", "status": "open",
            "applicants-TOTAL_FORMS": "0", "applicants-INITIAL_FORMS": "0", "applicants-MIN_NUM_FORMS": "0", "applicants-MAX_NUM_FORMS": "1000"})
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["form"].errors)
        resp = self.client.post(reverse("erp_ui:add", args=["hr", "trainingprogram"]), {
            "company": self.company.pk, "title": "Fire safety", "start_date": "2026-09-10", "cost": "0", "status": "planned",
            "participants-TOTAL_FORMS": "0", "participants-INITIAL_FORMS": "0", "participants-MIN_NUM_FORMS": "0", "participants-MAX_NUM_FORMS": "1000"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(TrainingProgram.objects.filter(title="Fire safety").exists())

    def test_new_hr_reports_run(self):
        DisciplinaryAction.objects.create(employee=self.emp, date=date(2026, 9, 1), action_type="verbal", reason="Late")
        program = TrainingProgram.objects.create(company=self.company, title="Safety", start_date=date(2026, 9, 1))
        TrainingParticipant.objects.create(program=program, employee=self.emp, attended=True)
        Vacancy.objects.create(company=self.company, title="Clerk")
        for key in ("employee_master", "headcount", "joiners_leavers", "attendance_summary", "leave_balances", "leave_register", "loan_outstanding",
                    "document_expiry", "disciplinary_register", "movements", "recruitment_pipeline", "training_summary",
                    "payroll_summary", "payroll_register", "salary_by_department", "salary_structures"):
            resp = self.client.get(reverse("reports_erp:report", args=[key]), {"from": "2026-01-01", "to": "2026-12-31", "as_of": "2026-09-30"})
            self.assertEqual(resp.status_code, 200, key)
            self.assertNotContains(resp, "Traceback", status_code=200)
