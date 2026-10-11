"""The legacy POS screens after the hardening/ledger changes (sale, payment, register close, stock adjustment, purchase order)."""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounting.reports import balance_of
from catalog.models import Product, Unit
from company.models import Company
from customers.models import Customer
from inventory.models import StockItem
from payments.models import Payment
from procurement import services as pr
from register.models import Register, RegisterSession
from sales.models import Sale
from stock import services as st
from stock.models import StockLedgerEntry
from suppliers.models import PurchaseOrder, Supplier
from tenants.models import Store, Warehouse

from .models import PosSettings


def D(x):
    return Decimal(str(x))


class PosViewBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.other_store = Store.objects.create(name="Other", company=self.company)
        self.wh = Warehouse.objects.create(store=self.store, name="Shop", code="SHOP", warehouse_type="OUTLET")
        unit = Unit.objects.create(store=self.store, name="Piece", short_name="pc", allow_decimal=True)
        self.v = Product.objects.create(store=self.store, unit=unit, name="Shirt", sku="SH1", product_type="single").variants.first()
        self.v.selling_price = D(100)
        self.v.cost_price = D(60)
        self.v.save()
        self.user = get_user_model().objects.create_superuser("boss", "b@example.com", "dev-only-pw-123", store=self.store)
        self.user.is_biller = True
        self.user.save()
        self.register = Register.objects.create(store=self.store, warehouse=self.wh, name="C1")
        self.session = RegisterSession.objects.create(register=self.register, opened_by=self.user, opening_cash=D(1000))
        st.post_stock_movement(self.v, self.wh, 10, reference_type="OPENING", unit_cost=60)
        self.client.force_login(self.user)
        s = self.client.session
        s["active_register_session_id"] = self.session.id
        s.save()

    def finalize(self, items, payments=None, **extra):
        body = {"items": items, "payments": payments if payments is not None else []}
        body.update(extra)
        return self.client.post(reverse("sales:pos_finalize"), data=json.dumps(body), content_type="application/json")

    def stock(self):
        return StockItem.objects.get(variant=self.v, warehouse=self.wh).quantity


class FinalizeSaleTests(PosViewBase):
    def test_good_sale_updates_stock_ledger_and_caps_overpayment(self):
        resp = self.finalize([{"variant_id": self.v.id, "quantity": 2, "unit_price": 100}], [{"method": "cash", "amount": 500}])
        self.assertEqual(resp.status_code, 200, resp.content)
        sale = Sale.objects.get()
        self.assertEqual(sale.total_amount, D(200))
        self.assertEqual(self.stock(), D(8))
        entry = StockLedgerEntry.objects.get(reference_type="POS_SALE")
        self.assertEqual((entry.reference_no, entry.quantity), (sale.reference, D(-2)))
        self.assertEqual(Payment.objects.get().amount, D(200))          # the extra 300 was change

    def test_bad_numbers_are_rejected_and_nothing_is_saved(self):
        for bad in ({"quantity": 0}, {"quantity": -1}, {"quantity": "abc"}, {"quantity": 1, "unit_price": -5},
                    {"quantity": 1, "discount_amount": 500}, {"quantity": 1, "discount_amount": -1}, {"quantity": "NaN"}):
            resp = self.finalize([{"variant_id": self.v.id, **bad}])
            self.assertEqual(resp.status_code, 400, bad)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(self.stock(), D(10))

    def test_insufficient_stock_rolls_back_everything(self):
        resp = self.finalize([{"variant_id": self.v.id, "quantity": 1}, {"variant_id": self.v.id, "quantity": 50}])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(self.stock(), D(10))

    def test_customer_from_another_branch_refused(self):
        stranger = Customer.objects.create(store=self.other_store, name="Not ours")
        resp = self.finalize([{"variant_id": self.v.id, "quantity": 1}], customer_id=stranger.id)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)

    def test_hold_does_not_touch_stock_or_payments(self):
        resp = self.finalize([{"variant_id": self.v.id, "quantity": 2}], [{"method": "cash", "amount": 50}], hold=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.stock(), D(10))
        self.assertEqual(Payment.objects.count(), 0)

    def test_price_override_is_audited(self):
        from audit.models import AuditLog
        self.finalize([{"variant_id": self.v.id, "quantity": 1, "unit_price": 80}])
        self.assertTrue(AuditLog.objects.filter(note__icontains="Price override").exists())

    def test_accounting_posts_only_when_switched_on(self):
        self.finalize([{"variant_id": self.v.id, "quantity": 1}], [{"method": "cash", "amount": 100}])
        self.assertEqual(balance_of(self.company, "sales_retail"), D(0))
        PosSettings.objects.create(company=self.company, post_to_accounting=True)
        self.finalize([{"variant_id": self.v.id, "quantity": 1}], [{"method": "cash", "amount": 100}])
        self.assertEqual(balance_of(self.company, "sales_retail"), D(100))
        self.assertEqual(balance_of(self.company, "cash"), D(100))

    def test_sale_still_succeeds_when_accounting_is_broken(self):
        from accounting.models import Account
        PosSettings.objects.create(company=self.company, post_to_accounting=True)
        Account.objects.filter(company=self.company, system_key="sales_retail").update(is_active=False)
        resp = self.finalize([{"variant_id": self.v.id, "quantity": 1}], [{"method": "cash", "amount": 100}])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Sale.objects.count(), 1)
        from .models import PosPostingIssue
        self.assertTrue(PosPostingIssue.objects.filter(resolved=False).exists())


class PaymentTests(PosViewBase):
    def make_sale(self):
        self.finalize([{"variant_id": self.v.id, "quantity": 2}], [{"method": "cash", "amount": 50}])
        return Sale.objects.get()

    def test_bad_payments_refused_good_one_accepted(self):
        sale = self.make_sale()
        url = reverse("sales:sale_add_payment", args=[sale.pk])
        for amount in ("0", "-5", "abc", "151"):
            self.client.post(url, {"amount": amount, "method": "cash"})
        self.client.post(url, {"amount": "10", "method": "bitcoin"})
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 1)
        self.client.post(url, {"amount": "150", "method": "card"})
        self.assertEqual(Payment.objects.filter(sale=sale).count(), 2)
        self.assertEqual(Sale.objects.get(pk=sale.pk).due_amount, D(0))


class RegisterCloseTests(PosViewBase):
    def test_bad_count_keeps_register_open_and_good_count_closes(self):
        self.client.post(reverse("register:close"), {"closing_cash_counted": "abc"})
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "open")
        self.client.post(reverse("register:close"), {"closing_cash_counted": "990"})
        self.session.refresh_from_db()
        self.assertEqual((self.session.status, self.session.closing_cash_counted), ("closed", D(990)))
        self.assertEqual(self.session.variance, D(-10))

    def test_over_short_booked_when_accounting_on(self):
        PosSettings.objects.create(company=self.company, post_to_accounting=True)
        self.client.post(reverse("register:close"), {"closing_cash_counted": "990"})
        self.assertEqual(balance_of(self.company, "cash_over_short"), D(10))

    def test_cash_refund_lowers_expected_cash(self):
        from . import services as ps
        self.finalize([{"variant_id": self.v.id, "quantity": 2}], [{"method": "cash", "amount": 200}])
        sale = Sale.objects.get()
        ps.process_pos_return(sale, [{"sale_item": sale.items.get(), "quantity": 1}], "x", user=self.user, session=self.session)
        self.assertEqual(self.session.expected_cash, D(1100))              # 1000 + 200 - 100 refunded


class StockAdjustmentTests(PosViewBase):
    def test_adjustment_goes_through_the_ledger_and_bad_rows_are_refused(self):
        url = reverse("inventory:quantity_adjustment")
        self.client.post(url, {"warehouse": self.wh.id, "reason": "recount", "rows_json": json.dumps([{"variant_id": self.v.id, "change": "abc"}])})
        self.assertEqual(self.stock(), D(10))
        self.client.post(url, {"warehouse": self.wh.id, "reason": "damage", "rows_json": json.dumps([{"variant_id": self.v.id, "change": "-3"}])})
        self.assertEqual(self.stock(), D(7))
        self.assertTrue(StockLedgerEntry.objects.filter(reference_type="POS_ADJUSTMENT", quantity=D(-3)).exists())


class LegacyPurchaseTests(PosViewBase):
    def form_data(self, supplier, status="received", qty="5"):
        return {"supplier": supplier.id, "warehouse": self.wh.id, "order_date": "2026-10-01", "status": status, "note": "",
                "items-TOTAL_FORMS": "1", "items-INITIAL_FORMS": "0", "items-MIN_NUM_FORMS": "0", "items-MAX_NUM_FORMS": "1000",
                "items-0-variant": self.v.id, "items-0-quantity": qty, "items-0-cost_price": "70"}

    def test_receiving_a_legacy_po_updates_stock_through_the_ledger(self):
        supplier = Supplier.objects.create(store=self.store, name="S")
        self.client.post(reverse("suppliers:purchase_add"), self.form_data(supplier))
        self.assertEqual(self.stock(), D(15))
        self.assertTrue(StockLedgerEntry.objects.filter(reference_type="PO_LEGACY", quantity=D(5)).exists())

    def test_invalid_lines_save_nothing(self):
        supplier = Supplier.objects.create(store=self.store, name="S")
        self.client.post(reverse("suppliers:purchase_add"), self.form_data(supplier, qty="abc"))
        self.assertEqual(PurchaseOrder.objects.count(), 0)
        self.assertEqual(self.stock(), D(10))

    def test_erp_managed_po_cannot_be_edited_here(self):
        supplier = Supplier.objects.create(store=self.store, name="S")
        po = pr.create_purchase_order(self.company, supplier, self.wh, [{"variant": self.v, "quantity": 5, "cost_price": 70}], user=self.user)
        data = self.form_data(supplier, status="received")
        data["items-INITIAL_FORMS"] = "0"
        self.client.post(reverse("suppliers:purchase_edit", args=[po.pk]), data)
        po.refresh_from_db()
        self.assertEqual(po.status, "draft")
        self.assertEqual(self.stock(), D(10))
