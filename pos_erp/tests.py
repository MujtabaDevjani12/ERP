from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase

from accounting.reports import balance_of
from accounting.models import JournalEntry
from catalog.models import Product, Unit
from company.models import Company
from configuration.models import NumberingPrefix
from core.errors import ERPError
from payments.models import Payment
from register.models import Register, RegisterSession
from sales.models import Sale, SaleItem
from stock import services as st
from tenants.models import Store, Warehouse

from . import hooks, retry, services as ps
from .models import PosPostingIssue, PosSettings


def D(x):
    return Decimal(str(x))


class PosBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.wh = Warehouse.objects.create(store=self.store, name="Shop", code="SHOP", warehouse_type="OUTLET")
        unit = Unit.objects.create(store=self.store, name="Piece", short_name="pc", allow_decimal=True)
        self.v = Product.objects.create(store=self.store, unit=unit, name="Shirt", sku="SH1", product_type="single").variants.first()
        self.v.selling_price = D(100)
        self.v.cost_price = D(60)
        self.v.save()
        self.user = get_user_model().objects.create_superuser("boss", "b@example.com", "dev-only-pw-123")
        self.register = Register.objects.create(store=self.store, warehouse=self.wh, name="C1")
        self.session = RegisterSession.objects.create(register=self.register, opened_by=self.user, opening_cash=D(1000))
        st.post_stock_movement(self.v, self.wh, 50, reference_type="OPENING", unit_cost=60)

    def make_sale(self, qty=2, price=100, pay=None, method="cash"):
        ref = NumberingPrefix.next_reference(NumberingPrefix.SALE)
        total = D(qty) * D(price)
        sale = Sale.objects.create(store=self.store, warehouse=self.wh, register_session=self.session, reference=ref, biller=self.user,
                                   status="completed", subtotal=total, total_amount=total)
        SaleItem.objects.create(sale=sale, variant=self.v, quantity=qty, unit_price=price)
        st.post_stock_movement(self.v, self.wh, -qty, reference_type="POS_SALE", reference_no=ref, allow_negative=True)
        payments = []
        if pay is None:
            pay = total
        if pay:
            payments.append(Payment.objects.create(sale=sale, reference=NumberingPrefix.next_reference(NumberingPrefix.PAYMENT),
                                                   amount=pay, method=method, received_by=self.user))
        return sale, payments

    def enable(self, **kw):
        obj, _ = PosSettings.objects.get_or_create(company=self.company)
        obj.post_to_accounting = True
        for k, v in kw.items():
            setattr(obj, k, v)
        obj.save()
        return obj


class PostingTests(PosBase):
    def test_on_by_default_posts_every_sale_to_journal_entries(self):
        sale, pays = self.make_sale()
        hooks.on_sale_completed(sale, pays)
        self.assertTrue(JournalEntry.objects.filter(company=self.company, source_type="pos_sale", source_id=str(sale.pk)).exists())
        self.assertTrue(JournalEntry.objects.filter(company=self.company, source_type="pos_payment").exists())
        self.assertEqual(balance_of(self.company, "sales_retail"), D(200))

    def test_switched_off_posts_nothing(self):
        obj, _ = PosSettings.objects.get_or_create(company=self.company)
        obj.post_to_accounting = False
        obj.save()
        sale, pays = self.make_sale()
        hooks.on_sale_completed(sale, pays)
        self.assertEqual(JournalEntry.objects.filter(company=self.company).count(), 0)

    def test_sale_and_cash_payment_post_correctly(self):
        self.enable()
        sale, pays = self.make_sale(qty=2, price=100)
        ps.on_sale_completed(sale, pays)
        self.assertEqual(balance_of(self.company, "sales_retail"), D(200))
        self.assertEqual(balance_of(self.company, "cash"), D(200))
        self.assertEqual(balance_of(self.company, "ar"), D(0))
        self.assertEqual(balance_of(self.company, "cogs"), D(120))          # 2 x 60 average cost
        self.assertEqual(balance_of(self.company, "inventory"), D(3000 - 120))

    def test_posting_is_idempotent(self):
        self.enable()
        sale, pays = self.make_sale()
        ps.on_sale_completed(sale, pays)
        n = JournalEntry.objects.count()
        ps.on_sale_completed(sale, pays)
        self.assertEqual(JournalEntry.objects.count(), n)

    def test_card_goes_to_bank_and_overpayment_is_capped(self):
        self.enable()
        sale, pays = self.make_sale(qty=1, price=100, pay=150, method="card")
        ps.on_sale_completed(sale, pays)
        self.assertEqual(balance_of(self.company, "bank"), D(100))
        self.assertEqual(balance_of(self.company, "ar"), D(0))

    def test_credit_sale_leaves_receivable(self):
        self.enable()
        sale, pays = self.make_sale(qty=1, price=100, pay=0)
        ps.on_sale_completed(sale, pays)
        self.assertEqual(balance_of(self.company, "ar"), D(100))

    def test_failure_never_blocks_and_is_logged_then_retried(self):
        self.enable()
        sale, pays = self.make_sale()
        from accounting.models import Account
        Account.objects.filter(company=self.company, system_key="sales_retail").update(is_active=False)
        hooks.on_sale_completed(sale, pays)                                   # must not raise
        self.assertTrue(PosPostingIssue.objects.filter(resolved=False).exists())
        Account.objects.filter(company=self.company, system_key="sales_retail").update(is_active=True)
        for issue in PosPostingIssue.objects.filter(resolved=False):
            retry.retry_issue(issue)
        self.assertEqual(balance_of(self.company, "sales_retail"), D(200))

    def test_backfill_command_dry_run_then_apply(self):
        sale, pays = self.make_sale()
        self.enable()
        res = retry.backfill(self.company)
        self.assertEqual(res["sales"], 1)
        self.assertEqual(JournalEntry.objects.count(), 0)
        retry.backfill(self.company, apply=True)
        self.assertEqual(balance_of(self.company, "sales_retail"), D(200))
        self.assertEqual(retry.backfill(self.company)["sales"], 0)


class ReturnTests(PosBase):
    def test_partial_then_full_return(self):
        self.enable()
        sale, pays = self.make_sale(qty=4, price=100)
        ps.on_sale_completed(sale, pays)
        item = sale.items.first()
        r1 = ps.process_pos_return(sale, [{"sale_item": item, "quantity": 1}], "wrong size", user=self.user, session=self.session)
        sale.refresh_from_db()
        self.assertEqual(sale.status, "partially_returned")
        self.assertEqual(r1.refund_amount, D(100))
        self.assertEqual(st.available_qty(self.v, self.wh), D(47))            # 50 - 4 + 1
        self.assertEqual(balance_of(self.company, "sales_returns"), D(100))
        self.assertEqual(balance_of(self.company, "cash"), D(300))
        with self.assertRaises(ERPError):
            ps.process_pos_return(sale, [{"sale_item": item, "quantity": 4}], "too many", user=self.user)
        ps.process_pos_return(sale, [{"sale_item": item, "quantity": 3}], "all back", user=self.user, session=self.session)
        sale.refresh_from_db()
        self.assertEqual(sale.status, "returned")
        self.assertEqual(balance_of(self.company, "cash"), D(0))
        self.assertEqual(balance_of(self.company, "cogs"), D(0))               # goods back, cost reversed
        self.assertEqual(st.available_qty(self.v, self.wh), D(50))

    def test_reason_required_and_bad_status_rejected(self):
        sale, _ = self.make_sale()
        item = sale.items.first()
        with self.assertRaises(ERPError):
            ps.process_pos_return(sale, [{"sale_item": item, "quantity": 1}], "  ", user=self.user)
        sale.status = "held"
        sale.save()
        with self.assertRaises(ERPError):
            ps.process_pos_return(sale, [{"sale_item": item, "quantity": 1}], "x", user=self.user)

    def test_threshold_needs_approver(self):
        self.enable(return_approval_threshold=D(50))
        sale, _ = self.make_sale(qty=2, price=100)
        item = sale.items.first()
        cashier = get_user_model().objects.create_user("cash1", password="dev-only-pw-123")
        with self.assertRaises(PermissionDenied):
            ps.process_pos_return(sale, [{"sale_item": item, "quantity": 1}], "defect", user=cashier)
        r = ps.process_pos_return(sale, [{"sale_item": item, "quantity": 1}], "defect", user=self.user)
        self.assertEqual(r.approved_by, self.user)

    def test_restock_off_keeps_stock(self):
        sale, _ = self.make_sale(qty=2, price=100)
        ps.process_pos_return(sale, [{"sale_item": sale.items.first(), "quantity": 1}], "damaged", user=self.user, restock=False)
        self.assertEqual(st.available_qty(self.v, self.wh), D(48))


class ShiftTests(PosBase):
    def test_cash_refund_and_drop_reduce_expected_cash(self):
        sale, _ = self.make_sale(qty=2, price=100)
        ps.process_pos_return(sale, [{"sale_item": sale.items.first(), "quantity": 1}], "x", user=self.user, session=self.session)
        ps.record_cash_drop(self.session, D(50), user=self.user, reason="to safe")
        self.assertEqual(ps.shift_cash_adjustments(self.session), D(150))

    def test_cannot_drop_more_than_the_till_or_zero(self):
        with self.assertRaises(ERPError):
            ps.record_cash_drop(self.session, D(0))
        with self.assertRaises(ERPError):
            ps.record_cash_drop(self.session, D(999999))

    def test_close_books_over_short(self):
        self.enable()
        sale, pays = self.make_sale(qty=1, price=100)
        ps.on_sale_completed(sale, pays)
        self.session.closing_cash_counted = D(1090)                            # expected 1100
        self.session.status = "closed"
        self.session.save()
        ps.on_register_closed(self.session, self.user)
        self.assertEqual(balance_of(self.company, "cash_over_short"), D(10))   # natural balance of expense = loss
        self.assertEqual(balance_of(self.company, "cash"), D(90))


class ReportTests(PosBase):
    def test_daily_summary(self):
        from django.utils import timezone
        self.make_sale(qty=1, price=100)
        self.make_sale(qty=2, price=100, method="card")
        res = ps.daily_sales_summary(self.store, timezone.localdate())
        self.assertEqual(res["count"], 2)
        self.assertEqual(res["total"], D(300))
        self.assertEqual(res["by_method"]["card"], D(200))
