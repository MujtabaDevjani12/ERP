from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase

from accounting.reports import balance_of, trial_balance
from accounting.services import create_journal_entry
from approvals import services as ap
from approvals.models import ApprovalLevel, ApprovalWorkflow
from catalog.models import Product, Unit
from company.models import Company
from core.errors import ERPError, InsufficientStock
from customers.models import Customer
from masters.models import CustomerProfile, PaymentTerm, TaxRate
from masters.services import ensure_customer_profile
from stock import services as st
from tenants.models import Store, Warehouse

from . import services as ds
from .models import SalesInvoice, SalesOrder


def D(x):
    return Decimal(str(x))


class SalesBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.wh = Warehouse.objects.create(store=self.store, name="FG", code="FG", warehouse_type="FINISHED_GOODS")
        unit = Unit.objects.create(store=self.store, name="Piece", short_name="pc", allow_decimal=True)
        self.shirt = Product.objects.create(store=self.store, unit=unit, name="Shirt", sku="SH", product_type="single").variants.first()
        self.shirt.selling_price = D(500)
        self.shirt.save()
        self.customer = Customer.objects.create(store=self.store, name="Big Retailer")
        self.profile = ensure_customer_profile(self.customer, self.company, credit_limit=D(0))
        self.user = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-x")
        st.post_stock_movement(self.shirt, self.wh, 100, reference_type="OPENING", unit_cost=300, user=self.user)
        create_journal_entry(self.company, voucher_type="OPENING", narration="opening fg", lines=[
            {"account": "fg_inventory", "debit": 30000}, {"account": "opening_balance_equity", "credit": 30000}])
        self.gst = TaxRate.objects.create(company=self.company, name="GST", rate=18)

    def bal(self, key):
        return balance_of(self.company, key)

    def order(self, qty=10, price=500, discount=0, tax=None):
        o = ds.create_sales_order(self.company, self.customer, self.wh,
                                  [{"variant": self.shirt, "quantity": qty, "unit_price": price, "discount_percent": discount}],
                                  user=self.user, tax_rate=tax)
        return ds.submit_sales_order(o, self.user)


class OrderToCashTests(SalesBase):
    def test_full_cycle_gl_and_stock(self):
        order = self.order(10, 500, discount=10, tax=self.gst)      # net 4500, tax 810, total 5310
        self.assertEqual((order.subtotal, order.discount_amount, order.tax_amount, order.total), (D("4500.00"), D("500.00"), D("810.00"), D("5310.00")))
        self.assertEqual(order.status, "confirmed")

        item = order.items.get()
        dc = ds.create_delivery(order, [{"order_item": item, "quantity": 10}], user=self.user, vehicle_no="LEB-123")
        self.assertEqual(st.available_qty(self.shirt, self.wh), D(90))
        self.assertEqual(self.bal("cogs"), D("3000.00"))
        self.assertEqual(self.bal("fg_inventory"), D("27000.00"))
        order.refresh_from_db()
        self.assertEqual(order.status, "delivered")

        inv = ds.create_invoice(order, [{"challan_line": dc.lines.get(), "quantity": 10}], user=self.user)
        self.assertEqual((inv.subtotal, inv.tax_amount, inv.total), (D("4500.00"), D("810.00"), D("5310.00")))
        self.assertEqual(self.bal("ar"), D("5310.00"))
        self.assertEqual(self.bal("sales_wholesale"), D("4500.00"))
        self.assertEqual(self.bal("output_tax"), D("810.00"))
        order.refresh_from_db()
        self.assertEqual(order.status, "closed")

        rec = ds.create_customer_receipt(self.company, self.customer, D("5310.00"), user=self.user, method="cash",
                                         allocations=ds.auto_allocate(self.company, self.customer, D("5310.00")))
        inv.refresh_from_db()
        self.assertEqual(inv.outstanding, D(0))
        self.assertEqual(self.bal("ar"), D(0))
        self.assertEqual(self.bal("cash"), D("5310.00"))
        self.assertTrue(trial_balance(self.company)["balanced"])
        margin = ds.gross_margin(self.company)
        self.assertEqual(margin["margin"], D("1500.00"))             # 4500 revenue - 3000 cost

    def test_partial_delivery_and_invoice_limits(self):
        order = self.order(10)
        item = order.items.get()
        dc1 = ds.create_delivery(order, [{"order_item": item, "quantity": 4}], user=self.user)
        order.refresh_from_db()
        self.assertEqual(order.status, "partially_delivered")
        with self.assertRaises(ERPError):
            ds.create_delivery(order, [{"order_item": item, "quantity": 7}], user=self.user)     # only 6 left
        with self.assertRaises(ERPError):
            ds.create_invoice(order, [{"challan_line": dc1.lines.get(), "quantity": 5}], user=self.user)   # only 4 delivered
        ds.create_invoice(order, [{"challan_line": dc1.lines.get(), "quantity": 4}], user=self.user)
        with self.assertRaises(ERPError):
            ds.create_invoice(order, [{"challan_line": dc1.lines.get(), "quantity": 1}], user=self.user)   # already billed

    def test_cannot_deliver_unconfirmed_or_without_stock(self):
        draft = ds.create_sales_order(self.company, self.customer, self.wh, [{"variant": self.shirt, "quantity": 1}], user=self.user)
        with self.assertRaises(ERPError):
            ds.create_delivery(draft, [{"order_item": draft.items.get(), "quantity": 1}], user=self.user)
        big = self.order(500)
        with self.assertRaises(InsufficientStock):
            ds.create_delivery(big, [{"order_item": big.items.get(), "quantity": 500}], user=self.user)
        self.assertEqual(st.available_qty(self.shirt, self.wh), D(100))

    def test_deliver_and_invoice_shortcut_and_cancel_invoice(self):
        order = self.order(5, tax=self.gst)
        dc, inv = ds.deliver_and_invoice(order, user=self.user)
        self.assertEqual(inv.total, D("2950.00"))                    # 2500 + 18%
        ds.cancel_invoice(inv, self.user, "wrong")
        inv.refresh_from_db()
        self.assertEqual((inv.status, self.bal("ar")), ("cancelled", D(0)))
        ds.reverse_delivery(dc, self.user, "wrong")
        self.assertEqual(st.available_qty(self.shirt, self.wh), D(100))
        self.assertEqual(self.bal("cogs"), D(0))
        order.refresh_from_db()
        self.assertEqual(order.status, "confirmed")


class ControlTests(SalesBase):
    def test_credit_limit_blocks_then_manager_override(self):
        self.profile.credit_limit = D(3000)
        self.profile.save()
        o = ds.create_sales_order(self.company, self.customer, self.wh, [{"variant": self.shirt, "quantity": 10, "unit_price": 500}], user=self.user)
        with self.assertRaises(ERPError):
            ds.submit_sales_order(o, self.user)
        o = ds.submit_sales_order(o, self.user, credit_override=True)    # superuser holds approve_document
        self.assertTrue(o.credit_override)
        self.assertEqual(o.status, "confirmed")

    def test_override_denied_without_permission(self):
        self.profile.credit_limit = D(100)
        self.profile.save()
        clerk = get_user_model().objects.create_user("clerk", "c@example.com", "pw-12345-x")
        o = ds.create_sales_order(self.company, self.customer, self.wh, [{"variant": self.shirt, "quantity": 1, "unit_price": 500}], user=clerk)
        with self.assertRaises(PermissionDenied):
            ds.submit_sales_order(o, clerk, credit_override=True)

    def test_exposure_counts_open_invoices_and_orders(self):
        o1 = self.order(2)                                                     # 1000 confirmed, not invoiced
        self.assertEqual(ds.customer_exposure(self.company, self.customer), D("1000.00"))
        ds.deliver_and_invoice(o1, user=self.user)
        self.assertEqual(ds.customer_exposure(self.company, self.customer), D("1000.00"))     # now as receivable

    def test_big_discount_needs_manager(self):
        clerk = get_user_model().objects.create_user("clerk", "c@example.com", "pw-12345-x")
        o = ds.create_sales_order(self.company, self.customer, self.wh,
                                  [{"variant": self.shirt, "quantity": 1, "unit_price": 500, "discount_percent": 25}], user=clerk)
        with self.assertRaises(PermissionDenied):
            ds.submit_sales_order(o, clerk)
        ds.submit_sales_order(o, self.user)                                    # manager can

    def test_order_approval_workflow(self):
        mgr = get_user_model().objects.create_user("mgr", "m@example.com", "pw-12345-x")
        wf = ApprovalWorkflow.objects.create(company=self.company, name="SO", document_type="sales_order", min_amount=0)
        ApprovalLevel.objects.create(workflow=wf, level=1, approver_user=mgr)
        o = ds.create_sales_order(self.company, self.customer, self.wh, [{"variant": self.shirt, "quantity": 1, "unit_price": 500}], user=self.user)
        o = ds.submit_sales_order(o, self.user)
        self.assertEqual(o.status, "pending_approval")
        with self.assertRaises(ERPError):
            ds.create_delivery(o, [{"order_item": o.items.get(), "quantity": 1}], user=self.user)
        ap.approve(ap.get_request(o, "sales_order"), mgr)
        o.refresh_from_db()
        self.assertEqual(o.status, "confirmed")

    def test_validation_of_prices_and_quantities(self):
        for bad in ({"quantity": 0}, {"quantity": -1}, {"quantity": 1, "unit_price": -5}, {"quantity": 1, "discount_percent": 101}):
            with self.assertRaises(ERPError):
                ds.create_sales_order(self.company, self.customer, self.wh, [{"variant": self.shirt, **bad}], user=self.user)

    def test_customer_from_other_company_rejected(self):
        other = Company.objects.create(name="Other")
        store2 = Store.objects.create(name="O", company=other)
        stranger = Customer.objects.create(store=store2, name="Stranger")
        with self.assertRaises(ERPError):
            ds.create_sales_order(self.company, stranger, self.wh, [{"variant": self.shirt, "quantity": 1}], user=self.user)


class ReceiptAndReturnTests(SalesBase):
    def invoiced(self, qty=10, tax=None):
        o = self.order(qty, tax=tax)
        return ds.deliver_and_invoice(o, user=self.user)[1]

    def test_receipt_validation_partial_and_reversal(self):
        inv = self.invoiced(10)                                             # 5000
        with self.assertRaises(ERPError):
            ds.create_customer_receipt(self.company, self.customer, 100, user=self.user, allocations=[(inv, D(6000))])
        ds.create_customer_receipt(self.company, self.customer, 2000, user=self.user, allocations=[(inv, D(2000))])
        inv.refresh_from_db()
        self.assertEqual(inv.outstanding, D("3000.00"))
        rec = inv.allocations.get().receipt
        ds.reverse_customer_receipt(rec, self.user, "bounced")
        inv.refresh_from_db()
        self.assertEqual(inv.outstanding, D("5000.00"))
        self.assertEqual(self.bal("ar"), D("5000.00"))

    def test_return_restocks_and_credits(self):
        inv = self.invoiced(10, tax=self.gst)                              # 5000 + 900
        line = inv.lines.get()
        ret = ds.create_sales_return(inv, [{"invoice_line": line, "quantity": 4, "restock": True}], "wrong size", user=self.user)
        self.assertEqual((ret.net_amount, ret.tax_amount, ret.credit_amount), (D("2000.00"), D("360.00"), D("2360.00")))
        self.assertEqual(st.available_qty(self.shirt, self.wh), D(94))
        self.assertEqual(self.bal("sales_returns"), D("-2000.00"))      # a debit: returns reduce revenue
        self.assertEqual(self.bal("ar"), D("5900.00") - D("2360.00"))
        self.assertEqual(self.bal("cogs"), D("1800.00"))                  # 3000 - 4 x 300 restocked
        inv.refresh_from_db()
        self.assertEqual(inv.outstanding, D("3540.00"))
        with self.assertRaises(ERPError):
            ds.create_sales_return(inv, [{"invoice_line": line, "quantity": 7}], "too many", user=self.user)
        ds.create_sales_return(inv, [{"invoice_line": line, "quantity": 6, "restock": False}], "damaged", user=self.user)   # no restock
        inv.refresh_from_db()
        self.assertEqual(inv.outstanding, D(0))
        self.assertEqual(st.available_qty(self.shirt, self.wh), D(94))
        self.assertTrue(trial_balance(self.company)["balanced"])

    def test_return_needs_reason_and_refund_pays_out_cash(self):
        inv = self.invoiced(2)
        line = inv.lines.get()
        with self.assertRaises(ERPError):
            ds.create_sales_return(inv, [{"invoice_line": line, "quantity": 1}], " ", user=self.user)
        ds.create_customer_receipt(self.company, self.customer, 1000, user=self.user, method="cash", allocations=[(inv, D(1000))])
        ds.create_sales_return(inv, [{"invoice_line": line, "quantity": 2}], "not wanted", user=self.user, refund_method="cash")
        self.assertEqual(self.bal("cash"), D("0.00"))                       # 1000 in, 1000 refunded

    def test_cannot_cancel_paid_invoice(self):
        inv = self.invoiced(1)
        ds.create_customer_receipt(self.company, self.customer, 100, user=self.user, allocations=[(inv, D(100))])
        with self.assertRaises(ERPError):
            ds.cancel_invoice(inv, self.user)


class QuotationAndReportTests(SalesBase):
    def test_quotation_to_order_and_expiry(self):
        q = ds.create_quotation(self.company, self.customer, self.wh, [{"variant": self.shirt, "quantity": 3, "unit_price": 450}],
                                user=self.user, valid_until=date.today() + timedelta(days=7))
        with self.assertRaises(ERPError):
            ds.convert_quotation(q, self.user)                             # not accepted yet
        ds.send_quotation(q, self.user)
        ds.accept_quotation(q, self.user)
        order = ds.convert_quotation(q, self.user)
        self.assertEqual(order.total, D("1350.00"))
        q.refresh_from_db()
        self.assertEqual(q.status, "converted")
        old = ds.create_quotation(self.company, self.customer, self.wh, [{"variant": self.shirt, "quantity": 1}],
                                  user=self.user, valid_until=date.today() - timedelta(days=1))
        with self.assertRaises(ERPError):
            ds.accept_quotation(old, self.user)

    def test_aging_and_statement(self):
        o = self.order(4)
        dc, inv = ds.deliver_and_invoice(o, user=self.user, invoice_date=date.today() - timedelta(days=50))
        SalesInvoice.objects.filter(pk=inv.pk).update(due_date=date.today() - timedelta(days=35))
        aging = ds.ar_aging(self.company)
        self.assertEqual(aging["total"], D("2000.00"))
        stmt = ds.customer_statement(self.company, self.customer)
        self.assertEqual(stmt["closing"], D("2000.00"))
        self.assertEqual(ds.sales_summary(self.company)[0]["total"], D("2000.00"))
