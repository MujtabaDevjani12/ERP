from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounting.services import create_journal_entry
from company.models import Company
from tenants.models import Store

from . import excel, kpis, registry


class ReportTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.admin = get_user_model().objects.create_superuser("boss", "b@example.com", "dev-only-pw-123", store=self.store)
        create_journal_entry(self.company, narration="capital", lines=[
            {"account": "cash", "debit": 1000}, {"account": "opening_balance_equity", "credit": 1000}])

    def test_every_report_runs_without_error_on_a_nearly_empty_company(self):
        from django.utils import timezone
        today = timezone.localdate()
        params = registry.Params(date_from=today.replace(day=1), date_to=today, as_of=today)
        for key in registry.REPORTS:
            report, headers, rows = registry.run(key, self.company, params)
            self.assertIsInstance(headers, list, key)
            for row in rows:
                self.assertEqual(len(row), len(headers), f"{key}: row/header width mismatch")

    def test_trial_balance_total_matches(self):
        from django.utils import timezone
        params = registry.Params(date_from=timezone.localdate(), date_to=timezone.localdate(), as_of=timezone.localdate())
        _, headers, rows = registry.run("trial_balance", self.company, params)
        self.assertEqual(rows[-1][3], Decimal("1000.00"))
        self.assertEqual(rows[-1][4], Decimal("1000.00"))

    def test_excel_export_is_a_real_workbook(self):
        data = excel.to_xlsx("Test", ["A", "B"], [["x", Decimal("1.5")]])
        self.assertTrue(data.startswith(b"PK"))                                  # xlsx is a zip file

    def test_dashboard_blocks_follow_permissions(self):
        blocks = kpis.dashboard(self.company, self.admin, store=self.store)
        titles = {b["title"] for b in blocks}
        self.assertIn("Finance", titles)
        nobody = get_user_model().objects.create_user("nobody", password="dev-only-pw-123", store=self.store)
        self.assertEqual(kpis.dashboard(self.company, nobody, store=self.store), [])

    def test_pages_load_and_permission_is_enforced(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("reports_erp:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("reports_erp:report", args=["trial_balance"])).status_code, 200)
        resp = self.client.get(reverse("reports_erp:report", args=["trial_balance"]) + "?format=xlsx")
        self.assertEqual(resp["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        nobody = get_user_model().objects.create_user("nobody", password="dev-only-pw-123", store=self.store)
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(reverse("reports_erp:report", args=["trial_balance"])).status_code, 403)
