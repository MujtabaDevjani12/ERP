from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounting.models import JournalEntry
from accounting.reports import trial_balance
from catalog.models import Product, Unit
from company.models import Company
from core.errors import ERPError, InsufficientStock
from inventory.models import StockItem
from tenants.models import Store, Warehouse

from . import services as st
from .models import ItemWarehouseCost, StockLedgerEntry, TransferOrder


def D(x):
    return Decimal(str(x))


class StockBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.wh = Warehouse.objects.create(store=self.store, name="Main WH", code="MAIN", warehouse_type="GENERAL")
        self.wh2 = Warehouse.objects.create(store=self.store, name="Shop", code="SHOP", warehouse_type="OUTLET")
        unit = Unit.objects.create(store=self.store, name="Piece", short_name="pc", allow_decimal=True)
        self.product = Product.objects.create(store=self.store, unit=unit, name="Shirt", sku="SH1", product_type="single")
        self.v = self.product.variants.first()
        self.user = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-x")


class MovementTests(StockBase):
    def test_in_out_and_weighted_average(self):
        st.post_stock_movement(self.v, self.wh, 10, reference_type="T", unit_cost=100)
        st.post_stock_movement(self.v, self.wh, 10, reference_type="T", unit_cost=200)
        self.assertEqual(st.avg_cost(self.v, self.wh), D("150"))
        out = st.post_stock_movement(self.v, self.wh, -5, reference_type="T")
        self.assertEqual(out.unit_cost, D("150"))                       # outbound valued at average
        self.assertEqual(st.available_qty(self.v, self.wh), D("15"))
        self.assertEqual(out.balance_after, D("15"))
        self.assertEqual(StockItem.objects.get(variant=self.v, warehouse=self.wh).quantity, D("15"))

    def test_cannot_go_negative_unless_allowed(self):
        st.post_stock_movement(self.v, self.wh, 2, reference_type="T", unit_cost=10)
        with self.assertRaises(InsufficientStock):
            st.post_stock_movement(self.v, self.wh, -3, reference_type="T")
        self.assertEqual(st.available_qty(self.v, self.wh), D("2"))     # nothing changed
        st.post_stock_movement(self.v, self.wh, -3, reference_type="POS", allow_negative=True)
        self.assertEqual(st.available_qty(self.v, self.wh), D("-1"))

    def test_zero_and_missing_reference_rejected(self):
        with self.assertRaises(ERPError):
            st.post_stock_movement(self.v, self.wh, 0, reference_type="T")
        with self.assertRaises(ERPError):
            st.post_stock_movement(self.v, self.wh, 1, reference_type="")

    def test_ledger_is_immutable(self):
        e = st.post_stock_movement(self.v, self.wh, 1, reference_type="T", unit_cost=1)
        e.remarks = "x"
        with self.assertRaises(RuntimeError):
            e.save()
        with self.assertRaises(RuntimeError):
            e.delete()
        with self.assertRaises(RuntimeError):
            StockLedgerEntry.objects.all().update(remarks="y")

    def test_batches(self):
        b = st.get_or_create_batch(self.v, "B1")
        st.post_stock_movement(self.v, self.wh, 5, reference_type="T", unit_cost=1, batch=b)
        with self.assertRaises(InsufficientStock):
            st.post_stock_movement(self.v, self.wh, -6, reference_type="T", batch=b)

    def test_valuation_and_barcode(self):
        st.post_stock_movement(self.v, self.wh, 4, reference_type="T", unit_cost=25)
        rows, total = st.stock_valuation(self.company)
        self.assertEqual(total, D("100.00"))
        self.assertEqual(st.resolve_barcode(self.v.barcode), self.v)
        self.assertEqual(st.resolve_barcode(self.v.sku.lower()), self.v)
        self.assertIsNone(st.resolve_barcode("nope"))

    def test_backfill_creates_matching_ledger(self):
        StockItem.objects.create(variant=self.v, warehouse=self.wh, quantity=D("7"))
        with self.assertRaises(Exception):
            call_command("check_stock_ledger", verbosity=0)
        call_command("backfill_stock_ledger", apply=True, verbosity=0)
        call_command("check_stock_ledger", verbosity=0)
        self.assertEqual(StockItem.objects.get(variant=self.v, warehouse=self.wh).quantity, D("7"))


class TransferTests(StockBase):
    def test_transfer_full_flow_with_journal(self):
        st.post_stock_movement(self.v, self.wh, 10, reference_type="T", unit_cost=100)
        order = st.create_transfer(self.wh, self.wh2, [{"variant": self.v, "quantity": 4}], user=self.user)
        order = st.submit_transfer(order, self.user)            # no workflow configured -> auto-approved
        self.assertEqual(order.status, TransferOrder.APPROVED)
        order = st.dispatch_transfer(order, self.user)
        self.assertEqual(order.status, TransferOrder.IN_TRANSIT)
        self.assertEqual(st.available_qty(self.v, self.wh), D("6"))
        self.assertEqual(st.available_qty(self.v, self.wh2), D("0"))
        order = st.receive_transfer(order, self.user)
        self.assertEqual(st.available_qty(self.v, self.wh2), D("4"))
        self.assertEqual(st.avg_cost(self.v, self.wh2), D("100"))
        names = [r["name"] for r in trial_balance(self.company)["rows"]]
        self.assertNotIn("Inventory In Transit", names)          # cleared back to zero

    def test_cannot_dispatch_unapproved_or_oversend(self):
        st.post_stock_movement(self.v, self.wh, 1, reference_type="T", unit_cost=1)
        order = st.create_transfer(self.wh, self.wh2, [{"variant": self.v, "quantity": 5}], user=self.user)
        with self.assertRaises(ERPError):
            st.dispatch_transfer(order, self.user)               # still draft
        order = st.submit_transfer(order, self.user)
        with self.assertRaises(InsufficientStock):
            st.dispatch_transfer(order, self.user)               # only 1 on hand

    def test_short_receipt_written_off(self):
        st.post_stock_movement(self.v, self.wh, 10, reference_type="T", unit_cost=100)
        order = st.create_transfer(self.wh, self.wh2, [{"variant": self.v, "quantity": 10}], user=self.user)
        order = st.dispatch_transfer(st.submit_transfer(order, self.user), self.user)
        item = order.items.first()
        st.receive_transfer(order, self.user, received={item.pk: D("8")})
        self.assertEqual(st.available_qty(self.v, self.wh2), D("8"))
        entry = JournalEntry.objects.get(source_type="transfer_in", source_id=str(order.pk))
        self.assertEqual(entry.total_debit, D("1000.00"))        # 800 to inventory + 200 shortage


class AdjustmentAndCountTests(StockBase):
    def test_adjustment_posts_on_approval_and_books_loss(self):
        st.post_stock_movement(self.v, self.wh, 10, reference_type="T", unit_cost=50)
        adj = st.create_adjustment(self.wh, [{"variant": self.v, "quantity_change": -3}], reason="damage", user=self.user)
        adj = st.submit_adjustment(adj, self.user)               # auto-approved -> posted
        adj.refresh_from_db()
        self.assertEqual(adj.status, "posted")
        self.assertEqual(st.available_qty(self.v, self.wh), D("7"))
        entry = JournalEntry.objects.get(source_type="stock_adjustment", source_id=str(adj.pk))
        self.assertEqual(entry.total_debit, D("150.00"))

    def test_count_creates_adjustment_for_differences_only(self):
        st.post_stock_movement(self.v, self.wh, 10, reference_type="T", unit_cost=10)
        count = st.open_stock_count(self.wh, user=self.user)
        st.record_count(count, {self.v.id: D("8")}, self.user)
        count, adj = st.submit_count(count, self.user)
        self.assertIsNotNone(adj)
        self.assertEqual(st.available_qty(self.v, self.wh), D("8"))
        count.refresh_from_db()
        self.assertEqual(count.status, "posted")
        with self.assertRaises(ERPError):
            st.record_count(count, {self.v.id: D("1")}, self.user)   # closed
