from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from company.models import Company
from tenants.models import Store, Warehouse


class FiscalYearTests(TestCase):
    def test_calendar_year_company(self):
        c = Company(name="A", fiscal_year_start_month=1)
        self.assertEqual(c.fiscal_year_for(date(2026, 1, 1)), 2026)
        self.assertEqual(c.fiscal_year_for(date(2026, 12, 31)), 2026)

    def test_july_start_company(self):
        c = Company(name="A", fiscal_year_start_month=7)
        self.assertEqual(c.fiscal_year_for(date(2026, 7, 1)), 2026)
        self.assertEqual(c.fiscal_year_for(date(2026, 8, 15)), 2026)
        self.assertEqual(c.fiscal_year_for(date(2026, 6, 30)), 2025)
        self.assertEqual(c.fiscal_year_for(date(2027, 3, 1)), 2026)


class StoreCompanyTests(TestCase):
    def test_store_company_is_optional_so_existing_pos_data_still_valid(self):
        store = Store.objects.create(name="Old Branch")
        self.assertIsNone(store.company)

    def test_warehouse_type_defaults_to_general(self):
        store = Store.objects.create(name="B")
        wh = Warehouse.objects.create(store=store, name="Main")
        self.assertEqual(wh.warehouse_type, Warehouse.GENERAL)


class BootstrapCompanyCommandTests(TestCase):
    def _run(self, *args):
        out = StringIO()
        call_command("bootstrap_company", *args, stdout=out)
        return out.getvalue()

    def test_links_orphan_stores_and_is_idempotent(self):
        s1 = Store.objects.create(name="Karachi")
        s2 = Store.objects.create(name="Lahore")
        self._run("--name", "Acme Textiles")
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(Company.objects.count(), 1)
        self.assertEqual(s1.company_id, s2.company_id)
        self.assertEqual(s1.company.name, "Acme Textiles")

        self._run()  # second run changes nothing
        self.assertEqual(Company.objects.count(), 1)

    def test_does_not_touch_store_that_already_has_company(self):
        other = Company.objects.create(name="Other Co")
        s = Store.objects.create(name="X", company=other)
        Store.objects.create(name="Orphan")
        self._run()
        s.refresh_from_db()
        self.assertEqual(s.company_id, other.id)
