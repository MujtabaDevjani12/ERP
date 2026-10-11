"""
End-to-end acceptance: one company runs a whole month through every module and the books must still balance.

  vendor -> PO -> goods receipt -> purchase invoice -> payment
  -> BOM -> work order -> material issue -> job card -> finished goods
  -> wholesale order -> delivery -> invoice -> customer receipt
  -> POS sale (accounting ON) and a POS return
  -> employee -> attendance -> payroll -> payment
and then: trial balance balances, balance sheet balances, ledger = stock, no failed POS postings.
"""
from datetime import date, time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from accounting.reports import balance_of, balance_sheet, profit_and_loss, trial_balance
from accounting.services import create_journal_entry
from catalog.models import Product, Unit
from company.models import Company
from configuration.models import NumberingPrefix
from customers.models import Customer
from distribution import services as ds
from hr import services as hr
from hr.models import Shift
from masters.models import TaxRate
from masters.services import ensure_customer_profile
from payments.models import Payment
from payroll import services as pay
from pos_erp import services as pos
from pos_erp.models import PosPostingIssue, PosSettings
from procurement import services as pr
from production import services as prod
from production.models import Operation
from register.models import Register, RegisterSession
from sales.models import Sale, SaleItem
from stock import services as st
from suppliers.models import Supplier
from tenants.models import Store, Warehouse


def D(x):
    return Decimal(str(x))


@override_settings(HR_WEEKLY_OFF_DAYS=(6,))
class MonthInTheLifeTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme Garments")
        call_command("seed_chart_of_accounts", company="Acme Garments", verbosity=0)
        self.store = Store.objects.create(name="Factory", company=self.company)
        self.raw = Warehouse.objects.create(store=self.store, name="Raw", code="RAW", warehouse_type="RAW_MATERIAL")
        self.fg = Warehouse.objects.create(store=self.store, name="FG", code="FG", warehouse_type="FINISHED_GOODS")
        self.shop = Warehouse.objects.create(store=self.store, name="Shop", code="SHOP", warehouse_type="OUTLET")
        unit = Unit.objects.create(store=self.store, name="Piece", short_name="pc", allow_decimal=True)

        def item(sku):
            return Product.objects.create(store=self.store, unit=unit, name=sku, sku=sku, product_type="single").variants.first()

        self.fabric, self.button, self.shirt = item("FAB"), item("BTN"), item("SHIRT")
        self.shirt.selling_price = D(800)
        self.shirt.save()
        self.user = get_user_model().objects.create_superuser("boss", "b@example.com", "dev-only-pw-123")
        self.supplier = Supplier.objects.create(store=self.store, name="Mill Co")
        self.customer = Customer.objects.create(store=self.store, name="Big Retailer")
        ensure_customer_profile(self.customer, self.company, credit_limit=D(0))
        self.gst = TaxRate.objects.create(company=self.company, name="GST", rate=18)
        create_journal_entry(self.company, voucher_type="OPENING", narration="owner capital", lines=[
            {"account": "cash", "debit": 1000000}, {"account": "opening_balance_equity", "credit": 1000000}])

    def bal(self, key):
        return balance_of(self.company, key)

    # ------------------------------------------------------------------
    def buy_materials(self):
        po = pr.create_purchase_order(self.company, self.supplier, self.raw, [
            {"variant": self.fabric, "quantity": 100, "cost_price": 100}, {"variant": self.button, "quantity": 500, "cost_price": 2}], user=self.user)
        pr.submit_purchase_order(po, self.user)
        po.refresh_from_db()
        grn = pr.receive_goods(po, [{"po_item": i, "quantity": i.quantity} for i in po.items.all()], user=self.user)
        self.assertEqual(self.bal("raw_inventory"), D("11000.00"))
        lines = [{"grn_line": gl, "quantity": gl.quantity_received, "unit_price": gl.unit_cost} for gl in grn.lines.all()]
        inv = pr.create_purchase_invoice(self.company, self.supplier, "MILL-001", date(2026, 9, 5), lines, user=self.user)
        pr.submit_purchase_invoice(inv, self.user)
        inv.refresh_from_db()
        self.assertEqual(self.bal("ap"), inv.total)
        payment = pr.create_vendor_payment(self.company, self.supplier, inv.total, user=self.user, method="cash", allocations=[(inv, inv.total)])
        pr.submit_vendor_payment(payment, self.user)
        self.assertEqual(self.bal("ap"), D(0))
        self.assertEqual(self.bal("grni"), D(0))

    def manufacture(self):
        sewing = Operation.objects.create(company=self.company, name="Sewing")
        bom = prod.create_bom(self.company, self.shirt, [{"component": self.fabric, "quantity": 2}, {"component": self.button, "quantity": 5}],
                              operations=[{"operation": sewing, "minutes_per_unit": 30, "labor_rate_per_hour": 60}], user=self.user)
        prod.activate_bom(bom, self.user)
        wo = prod.create_work_order(bom, 10, self.raw, self.fg, user=self.user)
        wo = prod.submit_work_order(wo, self.user)
        prod.issue_materials(wo, user=self.user)
        card = wo.job_cards.get()
        prod.start_job_card(card, user=self.user)
        prod.complete_job_card(card, 10, 0, user=self.user)
        prod.receive_finished_goods(wo, 10, user=self.user)
        prod.complete_work_order(wo, self.user)
        prod.close_work_order(wo, self.user)
        self.assertEqual(self.bal("wip_inventory"), D(0))
        self.assertEqual(st.available_qty(self.shirt, self.fg), D(10))
        self.assertEqual(st.avg_cost(self.shirt, self.fg), D("240.0000"))       # (2100 materials + 300 labour) / 10

    def wholesale(self):
        order = ds.create_sales_order(self.company, self.customer, self.fg, [{"variant": self.shirt, "quantity": 5, "unit_price": 500}],
                                      user=self.user, tax_rate=self.gst)
        order = ds.submit_sales_order(order, self.user)
        _, inv = ds.deliver_and_invoice(order, user=self.user)
        self.assertEqual(inv.total, D("2950.00"))                                # 2500 + 18%
        self.assertEqual(self.bal("ar"), D("2950.00"))
        self.assertEqual(self.bal("cogs"), D("1200.00"))
        ds.create_customer_receipt(self.company, self.customer, inv.total, user=self.user, method="cash",
                                   allocations=ds.auto_allocate(self.company, self.customer, inv.total))
        self.assertEqual(self.bal("ar"), D(0))

    def retail(self):
        st.post_stock_movement(self.shirt, self.shop, 3, reference_type="OPENING", unit_cost=240)
        create_journal_entry(self.company, voucher_type="OPENING", narration="shop stock", lines=[
            {"account": "inventory", "debit": 720}, {"account": "opening_balance_equity", "credit": 720}])
        PosSettings.objects.create(company=self.company, post_to_accounting=True)
        register = Register.objects.create(store=self.store, warehouse=self.shop, name="C1")
        session = RegisterSession.objects.create(register=register, opened_by=self.user, opening_cash=D(0))
        ref = NumberingPrefix.next_reference(NumberingPrefix.SALE)
        sale = Sale.objects.create(store=self.store, warehouse=self.shop, register_session=session, reference=ref, biller=self.user,
                                   status="completed", subtotal=D(1600), total_amount=D(1600))
        SaleItem.objects.create(sale=sale, variant=self.shirt, quantity=2, unit_price=800)
        st.post_stock_movement(self.shirt, self.shop, -2, reference_type="POS_SALE", reference_no=ref, allow_negative=True)
        payment = Payment.objects.create(sale=sale, reference=NumberingPrefix.next_reference(NumberingPrefix.PAYMENT), amount=1600,
                                         method="cash", received_by=self.user)
        pos.on_sale_completed(sale, [payment])
        self.assertEqual(self.bal("sales_retail"), D("1600.00"))
        pos.process_pos_return(sale, [{"sale_item": sale.items.get(), "quantity": 1}], "wrong size", user=self.user, session=session)
        self.assertEqual(self.bal("sales_returns"), D("800.00"))
        self.assertEqual(st.available_qty(self.shirt, self.shop), D(2))          # 3 - 2 + 1 returned

    def payroll(self):
        shift = Shift.objects.create(company=self.company, name="Day", start_time=time(9), end_time=time(18), standard_hours=8, half_day_hours=4)
        emp = hr.create_employee(self.company, "Ali Khan", date(2026, 1, 1), shift=shift)
        pay.set_salary(emp, basic=30000, tax_percent=5)
        for day in range(1, 31):
            d = date(2026, 9, day)
            if d.weekday() != 6:
                hr.record_attendance(emp, d, check_in=time(9), check_out=time(17, 30))
        run = pay.submit_payroll(pay.calculate_payroll(self.company, 2026, 9, user=self.user), self.user)
        self.assertEqual(run.status, "approved")
        pay.pay_payroll(run, self.user, method="cash")
        self.assertEqual(self.bal("salary_payable"), D(0))

    # ------------------------------------------------------------------
    def test_whole_month_keeps_the_books_straight(self):
        self.buy_materials()
        self.manufacture()
        self.wholesale()
        self.retail()
        self.payroll()

        tb = trial_balance(self.company)
        self.assertTrue(tb["balanced"], f"trial balance off: {tb['total_debit']} vs {tb['total_credit']}")
        sheet = balance_sheet(self.company, as_of=date(2026, 12, 31))
        self.assertTrue(sheet["balanced"], "balance sheet does not balance")

        for row in st.inventory_gl_vs_stock(self.company):
            self.assertEqual(row["difference"], D(0), f"ledger and stock disagree on {row['account']}")

        pnl = profit_and_loss(self.company, date_from=date(2020, 1, 1), date_to=date(2030, 1, 1))
        self.assertEqual(pnl["net_profit"], pnl["total_revenue"] - pnl["total_expenses"])
        self.assertFalse(PosPostingIssue.objects.filter(resolved=False).exists(), "a POS document failed to post")
