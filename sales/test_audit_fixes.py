"""Regression tests for the project audit: returned sales in the numbers, exact part-returns, price / discount / whole-unit rules."""
from decimal import Decimal
from types import SimpleNamespace

from django.urls import reverse

from core.pos_fixtures import PASSWORD
from sales.models import Sale
from sales.returns import line_parts, refund_value
from sales.stats import COUNTED, returns_total, unpaid_total
from sales.tests import PosBase


class ReturnedSalesInTheNumbersTests(PosBase):
    def sell(self, variant, qty, amount):
        data = self.checkout([self.line(variant, qty)], [{"method": "cash", "amount": amount}]).json()
        return Sale.objects.get(pk=data["sale_id"])

    def give_back(self, sale, qty):
        item = sale.items.get()
        self.client.post(reverse("sales:sale_return", args=[sale.pk]),
                         {f"qty_{item.pk}": str(qty), "reason": "damaged", "refund_method": "cash", "restock": "on"})
        sale.refresh_from_db()

    def test_part_returned_sale_still_counts_and_only_the_returned_goods_come_off(self):
        sale = self.sell(self.w.milk, 2, "100")
        self.give_back(sale, 1)
        self.assertEqual(sale.status, "partially_returned")
        self.assertIn(sale.status, COUNTED)
        back = returns_total(self.w.store)
        self.assertEqual(back["value"], Decimal("50.00"))
        self.assertEqual((sale.returned_amount, sale.net_total), (Decimal("50.00"), Decimal("50.00")))
        self.assertEqual(sale.due_amount, Decimal("0"))                          # paid 100, 50 of it handed back -> nothing owed

    def test_fully_returned_credit_sale_owes_nothing(self):
        sale = self.sell(self.w.milk, 1, "50")
        self.give_back(sale, 1)
        self.assertEqual(sale.status, "returned")
        self.assertEqual(sale.net_total, Decimal("0.00"))
        self.assertEqual(unpaid_total(Sale.objects.filter(pk=sale.pk), Decimal("50")), Decimal("0"))

    def test_dashboard_report_shows_net_sales_after_returns(self):
        sale = self.sell(self.w.milk, 2, "100")
        self.give_back(sale, 1)
        self.client.login(username="manager", password=PASSWORD)
        resp = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_revenue"], Decimal("50.00"))
        self.assertEqual(resp.context["sale_count"], 1)


class ExactPartReturnTests(PosBase):
    def test_part_returns_add_up_to_the_exact_line_value(self):
        item = SimpleNamespace(quantity=Decimal("3"), unit_price=Decimal("3.33"), discount_amount=Decimal("0"), tax_amount=Decimal("0.50"))
        parts = [refund_value(item, 1, already) for already in (Decimal("0"), Decimal("1"), Decimal("2"))]
        self.assertEqual(sum(parts), refund_value(item, 3))
        self.assertEqual(sum(parts), Decimal("10.49"))                           # 9.99 + 0.50, not a cent more
        net, tax = line_parts(item, 3)
        self.assertEqual((net, tax), (Decimal("9.99"), Decimal("0.50")))


class CashierLimitsTests(PosBase):
    def test_cashier_cannot_sell_below_the_shelf_price_but_manager_can(self):
        resp = self.checkout([self.line(self.w.milk, 1, unit_price="10")], [{"method": "cash", "amount": "10"}])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
        self.client.login(username="manager", password=PASSWORD)
        self.activate(self.client, self.session)
        resp = self.checkout([self.line(self.w.milk, 1, unit_price="10")], [{"method": "cash", "amount": "10"}])
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_cashier_discount_is_capped_manager_is_not(self):
        body = [self.line(self.w.milk, 1)]
        resp = self.checkout(body, [{"method": "cash", "amount": "30"}], order_discount={"type": "percent", "value": "40"})
        self.assertEqual(resp.status_code, 400)
        resp = self.checkout(body, [{"method": "cash", "amount": "45"}], order_discount={"type": "percent", "value": "10"})
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_whole_unit_products_refuse_a_fraction(self):
        resp = self.checkout([self.line(self.w.milk, "1.5")], [{"method": "cash", "amount": "75"}])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)
