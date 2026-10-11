import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld
from inventory.models import StockItem
from suppliers.models import PurchaseOrder, PurchaseOrderItem, Supplier


class PurchaseFormTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.store, self.wh = self.w.store, self.w.warehouse
        self.sup = Supplier.objects.create(store=self.store, name="Ali Traders")
        self.v1, self.v2 = self.w.tea, self.w.milk
        self.client.login(username="manager", password=PASSWORD)

    def post(self, rows, url=None, **extra):
        data = {"supplier": self.sup.pk, "warehouse": self.wh.pk, "order_date": "2026-10-01", "status": "draft", "note": "",
                "items-TOTAL_FORMS": len(rows), "items-INITIAL_FORMS": extra.pop("initial", 0), "items-MIN_NUM_FORMS": 0, "items-MAX_NUM_FORMS": 1000}
        for i, r in enumerate(rows):
            for k, v in r.items():
                data[f"items-{i}-{k}"] = v
        data.update(extra)
        return self.client.post(url or reverse("suppliers:purchase_add"), data)

    def test_lookup_returns_cost_for_the_picker(self):
        row = self.client.get(reverse("catalog:api_variant_lookup"), {"q": "BCTEA", "exact": "1"}).json()["results"][0]
        self.assertEqual(row["cost"], "10.00")
        self.assertTrue(row["tracked"])

    def test_form_page_renders_picker_and_seed(self):
        html = self.client.get(reverse("suppliers:purchase_add")).content.decode()
        self.assertIn("poForm()", html)
        self.assertIn("line-picker.js", html)
        self.assertIn('id="po-seed"', html)
        self.assertIn('x-model="status"', html)

    def test_create_with_lines_and_extra_fields(self):
        r = self.post([{"variant": self.v1.pk, "quantity": "10", "cost_price": "88.50"}, {"variant": self.v2.pk, "quantity": "2", "cost_price": "300"}],
                      supplier_ref="INV-77", expected_date="2026-10-05")
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["formset"].errors)
        po = PurchaseOrder.objects.get()
        self.assertEqual(po.supplier_ref, "INV-77")
        self.assertEqual(str(po.expected_date), "2026-10-05")
        self.assertEqual(po.total_amount, Decimal("10") * Decimal("88.50") + Decimal("600"))

    def test_an_order_needs_at_least_one_line(self):
        r = self.post([])
        self.assertEqual(r.status_code, 200)
        self.assertIn("Add at least one product", r.content.decode())
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_quantity_must_be_positive_and_error_comes_back_on_the_line(self):
        r = self.post([{"variant": self.v1.pk, "quantity": "0", "cost_price": "90"}])
        self.assertEqual(r.status_code, 200)
        seed = json.loads(r.content.decode().split('id="po-seed" type="application/json">')[1].split("</script>")[0])
        self.assertEqual(seed[0]["variant"], str(self.v1.pk))
        self.assertEqual(seed[0]["name"], str(self.v1))
        self.assertIn("more than zero", seed[0]["error"])

    def test_same_product_twice_is_refused(self):
        r = self.post([{"variant": self.v1.pk, "quantity": "1", "cost_price": "1"}, {"variant": self.v1.pk, "quantity": "2", "cost_price": "1"}])
        self.assertEqual(r.status_code, 200)
        self.assertIn("twice", r.content.decode())

    def test_expected_date_cannot_precede_order_date(self):
        r = self.post([{"variant": self.v1.pk, "quantity": "1", "cost_price": "1"}], expected_date="2026-09-01")
        self.assertEqual(r.status_code, 200)
        self.assertIn("cannot be before", r.content.decode())

    def test_edit_keeps_lines_and_can_delete_one_and_add_one(self):
        po = PurchaseOrder.objects.create(reference="PO-9", supplier=self.sup, warehouse=self.wh, order_date="2026-10-01", status="draft")
        a = PurchaseOrderItem.objects.create(purchase_order=po, variant=self.v1, quantity=5, cost_price=90)
        page = self.client.get(reverse("suppliers:purchase_edit", args=[po.pk])).content.decode()
        self.assertIn(str(self.v1), page)
        self.assertIn('name="items-INITIAL_FORMS" value="1"', page)
        r = self.post([{"id": a.pk, "purchase_order": po.pk, "variant": self.v1.pk, "quantity": "5", "cost_price": "90.00", "DELETE": "on"},
                       {"id": "", "purchase_order": po.pk, "variant": self.v2.pk, "quantity": "3", "cost_price": "300"}],
                      url=reverse("suppliers:purchase_edit", args=[po.pk]), initial=1)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(list(po.items.values_list("variant_id", flat=True)), [self.v2.pk])

    def test_received_status_adds_stock(self):
        r = self.post([{"variant": self.v1.pk, "quantity": "4", "cost_price": "90"}], status="received")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(StockItem.objects.get(variant=self.v1, warehouse=self.wh).quantity, 14)    # 10 on the shelf + 4 received


class SupplierScreenTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.store = self.w.store
        self.client.login(username="manager", password=PASSWORD)

    def test_create_with_the_new_fields_and_find_it_in_the_list(self):
        r = self.client.post(reverse("suppliers:supplier_add"), {
            "name": "Ali Traders", "company": "Ali Traders (Pvt) Ltd", "contact_person": "Ali", "phone": "0300-1", "email": "a@x.com",
            "tax_no": "1234567-8", "payment_term_days": 30, "address": "Main road", "city": "Lahore", "state": "Punjab",
            "postal_code": "54000", "country": "Pakistan", "notes": "Delivers Mondays", "is_active": "on"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["form"].errors)
        s = Supplier.objects.get()
        self.assertEqual((s.city, s.tax_no, s.payment_term_days), ("Lahore", "1234567-8", 30))
        html = self.client.get(reverse("suppliers:supplier_list"), {"q": "lahore"}).content.decode()
        self.assertIn("Ali Traders", html)
        self.assertIn("30 days", html)
        self.assertNotIn("Ali Traders", self.client.get(reverse("suppliers:supplier_list"), {"q": "karachi"}).content.decode())
        self.assertNotIn("Ali Traders", self.client.get(reverse("suppliers:supplier_list"), {"status": "inactive"}).content.decode())

    def test_form_and_empty_list_render(self):
        self.assertContains(self.client.get(reverse("suppliers:supplier_add")), "Payment terms")
        self.assertContains(self.client.get(reverse("suppliers:supplier_list")), "No suppliers yet")

    def test_prefilled_supplier_on_new_purchase(self):
        s = Supplier.objects.create(store=self.store, name="Z Co")
        html = self.client.get(reverse("suppliers:purchase_add"), {"supplier": s.pk}).content.decode()
        self.assertIn(f'<option value="{s.pk}" selected>', html)
