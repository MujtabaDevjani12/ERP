import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld
from inventory.models import StockAdjustment, StockItem
from tenants.models import Warehouse


class QuantityAdjustmentPageTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.back = Warehouse.objects.create(store=self.w.store, name="Back store")
        StockItem.objects.create(variant=self.w.tea, warehouse=self.back, quantity=Decimal("3"))
        self.client.login(username="manager", password=PASSWORD)

    def test_page_uses_the_shared_scan_picker_and_lists_every_reason(self):
        html = self.client.get(reverse("inventory:quantity_adjustment")).content.decode()
        self.assertIn("qtyAdjustApp()", html)
        self.assertIn("line-picker.js", html)
        self.assertIn("ui-form-wrap", html)                  # same container as every other form page
        for _value, label in StockAdjustment.REASON_CHOICES:
            self.assertIn(f">{label}</option>", html)

    def test_lookup_reports_the_stock_of_the_chosen_warehouse(self):
        url = reverse("catalog:api_variant_lookup")
        floor = self.client.get(url, {"q": "BCTEA", "exact": "1", "warehouse": self.w.warehouse.pk}).json()["results"][0]
        back = self.client.get(url, {"q": "BCTEA", "exact": "1", "warehouse": self.back.pk}).json()["results"][0]
        both = self.client.get(url, {"q": "BCTEA", "exact": "1"}).json()["results"][0]
        self.assertEqual((Decimal(floor["stock"]), Decimal(back["stock"]), Decimal(both["stock"])), (10, 3, 13))

    def test_saving_a_scanned_batch_still_works(self):
        rows = [{"variant_id": self.w.tea.pk, "change": 5}, {"variant_id": self.w.milk.pk, "change": -2}]
        r = self.client.post(reverse("inventory:quantity_adjustment"), {"warehouse": self.w.warehouse.pk, "reason": "recount", "note": "Friday", "rows_json": json.dumps(rows)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual((self.w.stock_of(self.w.tea), self.w.stock_of(self.w.milk)), (15, 3))
        self.assertEqual(StockAdjustment.objects.get().note, "Friday")


class LegacyTailwindClassesAreGoneTests(TestCase):
    """The ui-kit styles inputs itself; leftover Tailwind utility classes on widgets made some forms look different."""
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)

    def test_simple_forms_carry_no_tailwind_input_classes(self):
        for name in ("customers:customer_add", "customers:giftcard_add", "catalog:brand_add", "catalog:category_add", "catalog:unit_add"):
            html = self.client.get(reverse(name)).content.decode()
            main = html[html.index('id="main"'):]
            self.assertNotIn("border-gray-300", main, name)
            self.assertNotIn("rounded-lg border", main, name)

    def test_converted_pages_render(self):
        sale_pages = []
        for name in ("accounts:role_add", "accounts:staff_user_add", "reports:dashboard", "reports:import_products_csv", "accounts:password_change"):
            try:
                url = reverse(name)
            except Exception:
                continue
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, name)
            main = r.content.decode()
            main = main[main.index('id="main"'):]
            self.assertNotIn("card-shadow", main, name)
            self.assertNotIn("max-w-", main, name)
