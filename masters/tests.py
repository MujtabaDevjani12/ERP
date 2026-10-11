from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from openpyxl import Workbook

from accounting.models import Account
from catalog.models import Unit
from company.models import Company
from customers.models import Customer
from tenants.models import Store

from . import importing
from .models import CustomerProfile, ImportJob, PaymentTerm
from .services import compute_tax


def xlsx(header, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class TaxTests(TestCase):
    def test_exclusive_and_inclusive(self):
        self.assertEqual(compute_tax(1000, 18), (Decimal("1000.00"), Decimal("180.00"), Decimal("1180.00")))
        net, tax, gross = compute_tax(1180, 18, inclusive=True)
        self.assertEqual((net, tax, gross), (Decimal("1000.00"), Decimal("180.00"), Decimal("1180.00")))
        self.assertEqual(compute_tax(500, 0)[1], Decimal("0.00"))


class ImportTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        self.store = Store.objects.create(name="Main", company=self.company)
        Unit.objects.create(store=self.store, name="Piece", short_name="pc")
        PaymentTerm.objects.create(company=self.company, name="Net 30", days=30)

    def test_customer_import_valid(self):
        job = importing.create_import_job(
            self.company, "customers",
            xlsx(["name", "phone", "credit_limit", "payment_term"], [["Ali Traders", "0300", 5000, "Net 30"]]))
        self.assertEqual(job.status, ImportJob.VALIDATED, job.errors)
        importing.confirm_import(job)
        self.assertEqual(Customer.objects.filter(name="Ali Traders").count(), 1)
        self.assertEqual(CustomerProfile.objects.get().credit_limit, Decimal("5000"))

    def test_one_bad_row_blocks_everything(self):
        job = importing.create_import_job(
            self.company, "customers",
            xlsx(["name", "credit_limit"], [["Good", 1], ["", 2], ["Bad", "abc"]]))
        self.assertEqual(job.status, ImportJob.FAILED)
        self.assertEqual({e["row"] for e in job.errors}, {3, 4})
        with self.assertRaises(Exception):
            importing.confirm_import(job)
        self.assertEqual(Customer.objects.count(), 0)

    def test_missing_column_and_unknown_type(self):
        job = importing.create_import_job(self.company, "customers", xlsx(["phone"], [["1"]]))
        self.assertEqual(job.status, ImportJob.FAILED)
        with self.assertRaises(Exception):
            importing.create_import_job(self.company, "nope", xlsx(["a"], []))

    def test_item_import_creates_product_variant_profile(self):
        job = importing.create_import_job(
            self.company, "items",
            xlsx(["sku", "name", "unit", "selling_price", "item_type", "batch_required"],
                 [["YARN-1", "Cotton Yarn", "pc", 120, "raw_material", "yes"]]))
        self.assertEqual(job.status, ImportJob.VALIDATED, job.errors)
        importing.confirm_import(job)
        from catalog.models import ProductVariant
        v = ProductVariant.objects.get(sku="YARN-1-DEF")
        self.assertEqual(v.selling_price, Decimal("120"))
        self.assertTrue(v.erp_profile.batch_required)
        self.assertEqual(v.erp_profile.item_type, "raw_material")

    def test_duplicate_sku_in_file_rejected(self):
        job = importing.create_import_job(
            self.company, "items",
            xlsx(["sku", "name", "unit"], [["A", "A1", "pc"], ["A", "A2", "pc"]]))
        self.assertEqual(job.status, ImportJob.FAILED)

    def test_unbalanced_opening_balances_import_fails_whole(self):
        from accounting.management.commands.seed_chart_of_accounts import seed_chart
        seed_chart(self.company)
        cash = Account.objects.get(company=self.company, system_key="cash")
        job = importing.create_import_job(
            self.company, "opening_balances",
            xlsx(["account_code", "debit", "credit"], [[cash.code, 100, 0]]))
        self.assertEqual(job.status, ImportJob.VALIDATED)    # file is well formed
        with self.assertRaises(Exception):                   # but an unbalanced journal is refused
            importing.confirm_import(job)
