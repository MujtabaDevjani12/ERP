"""Every ERP list screen must render (with and without data), apply its filters, paginate and show real rows."""
import datetime
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.pos_fixtures import PASSWORD, PosWorld
from customers.models import Customer
from documents.models import DocumentLog
from inventory.models import StockAdjustment
from register import services
from sales.models import Sale
from suppliers.models import PurchaseOrder, Supplier

LISTS = [
    "sales:sale_list", "customers:customer_list", "catalog:product_list", "inventory:stock_list", "inventory:adjustment_list",
    "suppliers:purchase_list", "documents:document_log_list", "register:session_list", "accounts:staff_user_list",
    "accounts:role_list", "register:counter_list", "payments:method_list",
]


class ListScreenTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)

    def get(self, name, **params):
        resp = self.client.get(reverse(name), params)
        self.assertEqual(resp.status_code, 200, f"{name} {params}: {resp.status_code}")
        return resp

    def make_sale(self):
        session = services.open_session(self.w.counter1, self.w.manager, 0)
        s = self.client.session
        s["active_register_session_id"] = session.pk
        s.save()
        body = {"items": [{"variant_id": self.w.tea.pk, "quantity": "2"}], "payments": [{"method": "cash", "amount": "220"}]}
        resp = self.client.post(reverse("sales:pos_finalize"), data=json.dumps(body), content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        return Sale.objects.get()

    def test_every_list_renders_when_empty(self):
        for name in LISTS:
            self.get(name)

    def test_sales_list_shows_rows_summary_filters_and_row_menu(self):
        sale = self.make_sale()
        html = self.get("sales:sale_list").content.decode()
        self.assertIn(sale.reference, html)
        self.assertIn("Walk-in customer", html)
        self.assertIn("x-teleport", html)                 # the actions menu is not clipped by the table
        self.assertIn("ui-table", html)
        self.assertNotIn("datatable", html)               # no client-side DataTables dependency any more
        ctx = self.get("sales:sale_list").context
        self.assertEqual(ctx["sum_orders"], 1)
        self.assertEqual(ctx["sum_total"], Decimal("220.00"))
        self.assertEqual(ctx["sum_due"], Decimal("0.00"))
        self.assertIn(sale.reference, self.get("sales:sale_list", q=sale.reference[-3:]).content.decode())
        self.assertNotIn(sale.reference, self.get("sales:sale_list", q="zzzz").content.decode())
        self.assertNotIn(sale.reference, self.get("sales:sale_list", status="held").content.decode())
        today = timezone.localdate().isoformat()          # the date the app itself uses (store time zone), not this computer's
        self.assertIn(sale.reference, self.get("sales:sale_list", df=today, dt=today).content.decode())
        self.assertNotIn(sale.reference, self.get("sales:sale_list", df="2999-01-01").content.decode())

    def test_pagination_keeps_filters_and_clamps_page_size(self):
        self.make_sale()
        for i in range(30):
            Customer.objects.create(store=self.w.store, name=f"Customer {i:02d}", phone=f"0300{i:07d}")
        page1 = self.get("customers:customer_list")
        self.assertEqual(len(page1.context["customers"]), 25)
        self.assertEqual(page1.context["result_count"], 30)
        self.assertEqual(len(self.get("customers:customer_list", page=2).context["customers"]), 5)
        self.assertEqual(len(self.get("customers:customer_list", per=100).context["customers"]), 30)
        self.assertEqual(self.get("customers:customer_list", per=7).context["per"], 25)          # unknown sizes fall back
        self.assertEqual(len(self.get("customers:customer_list", q="Customer 1").context["customers"]), 10)
        self.assertEqual(self.client.get(reverse("customers:customer_list"), {"page": "abc"}).status_code, 404)

    def test_customers_search_and_status(self):
        Customer.objects.create(store=self.w.store, name="Ayesha", phone="0300111", is_active=True)
        Customer.objects.create(store=self.w.store, name="Bilal", phone="0300222", is_active=False)
        self.assertEqual([c.name for c in self.get("customers:customer_list", q="Ayesha").context["customers"]], ["Ayesha"])
        self.assertEqual([c.name for c in self.get("customers:customer_list", status="inactive").context["customers"]], ["Bilal"])
        self.assertEqual([c.name for c in self.get("customers:customer_list", q="0300222").context["customers"]], ["Bilal"])

    def test_products_list_filters_by_stock_and_keeps_out_of_stock_visible(self):
        names = lambda **p: sorted(x.name for x in self.get("catalog:product_list", **p).context["products"])
        self.assertEqual(names(), ["Empty shelf", "Milk", "Tea"])             # out-of-stock products stay visible in the ERP
        self.assertEqual(names(stock="out"), ["Empty shelf"])
        self.assertEqual(names(q="BCMILK"), ["Milk"])                          # barcode search through the variant
        self.assertEqual(names(q="TEA-1"), ["Tea"])
        self.assertEqual(names(status="inactive"), [])
        html = self.get("catalog:product_list").content.decode()
        self.assertIn("Out of stock", html)

    def test_stock_list_low_stock_and_warehouse_filters(self):
        rows = lambda **p: sorted(s.variant.product.name for s in self.get("inventory:stock_list", **p).context["stock_items"])
        self.assertEqual(rows(), ["Empty shelf", "Milk", "Tea"])
        self.assertEqual(rows(q="milk"), ["Milk"])
        self.assertIn("Empty shelf", rows(low="1"))
        self.assertEqual(rows(warehouse=self.w.warehouse.pk), ["Empty shelf", "Milk", "Tea"])
        self.assertEqual(rows(warehouse=999999), [])

    def test_other_lists_show_their_rows(self):
        sale = self.make_sale()
        sup = Supplier.objects.create(store=self.w.store, name="Acme Traders")
        PurchaseOrder.objects.create(reference="PO-1", supplier=sup, warehouse=self.w.warehouse, order_date=datetime.date.today())
        StockAdjustment.objects.create(reference="ADJ-1", warehouse=self.w.warehouse, reason="recount")
        DocumentLog.objects.create(sale=sale, channel="email", recipient="a@b.com", status="failed", error_message="SMTP down")
        self.assertIn("PO-1", self.get("suppliers:purchase_list").content.decode())
        self.assertNotIn("PO-1", self.get("suppliers:purchase_list", q="nomatch").content.decode())
        self.assertIn("PO-1", self.get("suppliers:purchase_list", status="draft").content.decode())
        self.assertIn("ADJ-1", self.get("inventory:adjustment_list").content.decode())
        self.assertNotIn("ADJ-1", self.get("inventory:adjustment_list", q="nomatch").content.decode())
        html = self.get("documents:document_log_list").content.decode()
        self.assertIn("SMTP down", html)
        self.assertNotIn("a@b.com", self.get("documents:document_log_list", status="sent").content.decode())
        self.assertIn(sale.register_session.register.name, self.get("register:session_list").content.decode())
        self.assertEqual(self.get("register:session_list", status="closed").context["result_count"], 0)
        users = self.get("accounts:staff_user_list", q="cash").context
        self.assertEqual(sorted(u.username for u in users["staff_users"]), ["cashier", "cashier2"])
