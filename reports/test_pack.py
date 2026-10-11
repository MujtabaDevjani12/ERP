"""Report pack: numbers match the sales they come from, scoping, permissions, CSV."""
import csv
import io
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld
from customers.models import Customer
from expenses.models import Expense, ExpenseCategory
from payments.models import Payment
from sales.models import Sale, SaleItem
from tenants.models import Store, Warehouse


class ReportPackTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.cust = Customer.objects.create(store=self.w.store, name="Ali Khan", phone="0300")
        self.s1 = self.sale("S-1", self.w.tea, 2, "100", customer=self.cust, paid="150")        # total 200, paid 150
        self.s2 = self.sale("S-2", self.w.milk, 3, "50", paid="150")                           # walk-in, total 150
        self.client.login(username="manager", password=PASSWORD)

    def sale(self, ref, variant, qty, price, customer=None, paid="0", status="completed"):
        total = Decimal(price) * Decimal(qty)
        s = Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, reference=ref, biller=self.w.manager, customer=customer, status=status,
                                subtotal=total, total_amount=total)
        SaleItem.objects.create(sale=s, variant=variant, quantity=qty, unit_price=Decimal(price))
        if Decimal(paid):
            Payment.objects.create(sale=s, reference=f"P-{ref}", amount=Decimal(paid), method="cash", received_by=self.w.manager)
        return s

    def get(self, slug, **q):
        return self.client.get(reverse("reports:pack_report", args=[slug]), q)

    def test_index_lists_every_report(self):
        r = self.client.get(reverse("reports:pack_index"))
        for title in ("Sales", "Products", "Payments", "Stock value", "Profit &amp; loss", "Customers"):
            self.assertContains(r, title)

    def test_every_report_renders_and_exports(self):
        from reports.pack import REGISTRY
        for slug in REGISTRY:
            self.assertEqual(self.get(slug).status_code, 200, slug)
            r = self.client.get(reverse("reports:pack_report_csv", args=[slug]))
            self.assertEqual(r.status_code, 200, slug)
            self.assertIn("text/csv", r["Content-Type"])

    def test_sales_totals(self):
        rows = self.get("sales").context["data"]["rows"]
        self.assertEqual((len(rows), rows[0]["n"], rows[0]["total"], rows[0]["paid"], rows[0]["due"]), (1, 2, 350, 300, 50))

    def test_held_and_other_branch_sales_are_ignored(self):
        self.sale("S-3", self.w.tea, 1, "100", status="held")
        other = Store.objects.create(name="Other", company=self.w.company)
        ow = Warehouse.objects.create(store=other, name="O")
        Sale.objects.create(store=other, warehouse=ow, reference="X-1", biller=self.w.manager, status="completed", total_amount=999)
        self.assertEqual(self.get("sales").context["data"]["rows"][0]["total"], 350)

    def test_biller_filter(self):
        self.assertEqual(self.get("sales", biller=self.w.cashier.pk).context["data"]["rows"], [])
        self.assertEqual(len(self.get("sales", biller=self.w.manager.pk).context["data"]["rows"]), 1)

    def test_products_profit_uses_cost(self):
        rows = {r["sku"]: r for r in self.get("products").context["data"]["rows"]}
        tea = rows["TEA-1"]
        self.assertEqual((tea["qty"], tea["revenue"], tea["cost"], tea["profit"]), (2, 200, 20, 180))          # fixture cost price is 10

    def test_payments_and_customers(self):
        pay = self.get("payments").context["data"]["rows"]
        self.assertEqual((pay[0]["n"], pay[0]["received"]), (2, 300))
        cust = self.get("customers").context["data"]
        self.assertEqual([(r["customer"], r["total"], r["due"]) for r in cust["rows"]], [("Ali Khan", 200, 50)])
        self.assertEqual(dict((k, v) for k, _t, v in cust["kpis"])["Walk-in sales"], 150)

    def test_stock_value(self):
        data = self.get("stock-value").context["data"]
        value = {r["sku"]: r["value"] for r in data["rows"]}
        self.assertEqual(value["TEA-1"], 100)                                                                  # 10 on hand x cost 10
        self.assertEqual(dict((k, v) for k, _t, v in data["kpis"])["Stock at cost"], 150)                      # tea 100 + milk 50
        self.assertEqual(len(self.get("stock-value", warehouse=Warehouse.objects.create(store=self.w.store, name="Empty").pk).context["data"]["rows"]), 0)

    def test_profit_loss(self):
        cat = ExpenseCategory.objects.create(store=self.w.store, name="Rent")
        from django.utils import timezone
        Expense.objects.create(store=self.w.store, category=cat, reference="E-1", amount=Decimal("40"), expense_date=timezone.localdate())
        kp = dict((k, v) for k, _t, v in self.get("profit-loss").context["data"]["kpis"])
        self.assertEqual((kp["Sales"], kp["Gross profit"], kp["Expenses"], kp["Net profit"]), (350, 350 - 50, 40, 350 - 50 - 40))   # cost: 2x10 + 3x10 = 50

    def test_csv_content_and_formula_safety(self):
        self.cust.name = "=HYPERLINK(1)"
        self.cust.save()
        body = self.client.get(reverse("reports:pack_report_csv", args=["customers"])).content.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(body)))
        self.assertEqual(rows[0][0], "Customer")
        self.assertEqual(rows[1][0], "'=HYPERLINK(1)")

    def test_date_range_and_bad_input(self):
        self.assertEqual(self.get("sales", **{"from": "2001-01-01", "to": "2001-01-31"}).context["data"]["rows"], [])
        self.assertEqual(self.get("sales", **{"from": "garbage", "to": "nope"}).status_code, 200)
        self.assertEqual(self.get("sales", **{"from": "2030-02-01", "to": "2030-01-01"}).status_code, 200)         # reversed range is swapped
        self.assertEqual(self.client.get(reverse("reports:pack_report", args=["nope"])).status_code, 404)

    def test_permissions(self):
        self.client.logout(); self.client.login(username="cashier", password=PASSWORD)        # cashiers may view sales, nothing else here
        for slug in ("stock-value", "profit-loss", "customers", "payments"):
            self.assertEqual(self.get(slug).status_code, 403, slug)
            self.assertEqual(self.client.get(reverse("reports:pack_report_csv", args=[slug])).status_code, 403, slug)
        self.assertEqual(self.get("sales").status_code, 200)
        index = self.client.get(reverse("reports:pack_index"))
        self.assertContains(index, "Best sellers"); self.assertNotContains(index, "Stock on hand is worth")
        self.client.logout()
        self.assertEqual(self.get("sales").status_code, 302)                                      # anonymous -> login
