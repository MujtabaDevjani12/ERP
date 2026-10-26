from datetime import date

from django.test import TestCase

from company.models import Company
from numbering.models import DocumentSequence
from numbering.services import next_reference
from tenants.models import Store


class NextReferenceTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme", fiscal_year_start_month=1)
        self.store = Store.objects.create(name="Karachi", company=self.company)

    def test_default_format_and_increment(self):
        d = date(2026, 5, 1)
        self.assertEqual(next_reference(self.company, "purchase_order", on_date=d), "PO-2026-00001")
        self.assertEqual(next_reference(self.company, "purchase_order", on_date=d), "PO-2026-00002")

    def test_employee_number_has_no_year_and_never_resets(self):
        self.assertEqual(next_reference(self.company, "employee", on_date=date(2026, 5, 1)), "EMP-00001")
        self.assertEqual(next_reference(self.company, "employee", on_date=date(2027, 5, 1)), "EMP-00002")

    def test_counter_resets_each_fiscal_year(self):
        self.assertEqual(next_reference(self.company, "sales_invoice", on_date=date(2026, 12, 31)), "INV-2026-00001")
        self.assertEqual(next_reference(self.company, "sales_invoice", on_date=date(2027, 1, 1)), "INV-2027-00001")

    def test_fiscal_year_follows_company_start_month(self):
        self.company.fiscal_year_start_month = 7
        self.company.save()
        self.assertEqual(next_reference(self.company, "journal_voucher", on_date=date(2026, 3, 1)), "JV-2025-00001")
        self.assertEqual(next_reference(self.company, "journal_voucher", on_date=date(2026, 7, 1)), "JV-2026-00001")

    def test_branch_specific_sequence_overrides_company_wide(self):
        d = date(2026, 2, 1)
        DocumentSequence.objects.create(
            company=self.company, store=self.store, document_type="sales_order",
            fiscal_year=2026, prefix="KHI-SO", padding=4,
        )
        self.assertEqual(next_reference(self.company, "sales_order", store=self.store, on_date=d), "KHI-SO-2026-0001")
        # company-wide (no store) is a separate counter
        self.assertEqual(next_reference(self.company, "sales_order", on_date=d), "SO-2026-00001")

    def test_companies_have_independent_counters(self):
        other = Company.objects.create(name="Other", fiscal_year_start_month=1)
        d = date(2026, 1, 5)
        self.assertEqual(next_reference(self.company, "goods_receipt", on_date=d), "GRN-2026-00001")
        # each company counts from 1 on its own, but the text differs (references are unique across the whole database)
        self.assertEqual(next_reference(other, "goods_receipt", on_date=d), f"GRNC{other.pk}-2026-00001")

    def test_admin_can_edit_next_number(self):
        d = date(2026, 1, 5)
        next_reference(self.company, "payroll", on_date=d)
        DocumentSequence.objects.filter(company=self.company, document_type="payroll").update(next_number=100)
        self.assertEqual(next_reference(self.company, "payroll", on_date=d), "PAY-2026-00100")

    def test_unknown_type_and_missing_company_rejected(self):
        with self.assertRaises(ValueError):
            next_reference(self.company, "banana")
        with self.assertRaises(ValueError):
            next_reference(None, "purchase_order")


class TwoCompaniesTests(TestCase):
    """Document references are unique across the whole database, so two companies must never be handed the same number."""

    def test_second_company_gets_its_own_prefix(self):
        a = Company.objects.create(name="A", fiscal_year_start_month=1)
        b = Company.objects.create(name="B", fiscal_year_start_month=1)
        d = date(2026, 5, 1)
        ra, rb = next_reference(a, "purchase_order", on_date=d), next_reference(b, "purchase_order", on_date=d)
        self.assertEqual(ra, "PO-2026-00001")
        self.assertNotEqual(ra, rb)
        self.assertTrue(rb.startswith("POC"))
        self.assertEqual(next_reference(b, "purchase_order", on_date=d), rb[:-1] + "2")

    def test_new_year_keeps_the_prefix_of_the_company(self):
        a = Company.objects.create(name="A", fiscal_year_start_month=1)
        b = Company.objects.create(name="B", fiscal_year_start_month=1)
        next_reference(a, "purchase_order", on_date=date(2026, 5, 1))
        next_reference(b, "purchase_order", on_date=date(2026, 5, 1))
        self.assertEqual(next_reference(a, "purchase_order", on_date=date(2027, 1, 2)), "PO-2027-00001")
        self.assertTrue(next_reference(b, "purchase_order", on_date=date(2027, 1, 2)).startswith("POC"))
