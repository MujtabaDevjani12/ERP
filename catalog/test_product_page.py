"""The add / edit product page: P1-style cards, plain-JS hooks, EAN generator, optional SKU, stock-now card."""
import re
from django.urls import reverse

from catalog.models import Category, Product
from django.test import TestCase

from catalog.tests import product_post, variant_row
from core.pos_fixtures import PASSWORD, PosWorld


class ProductPageTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        self.add = reverse("catalog:product_add")

    def test_add_page_has_the_p1_cards_and_no_alpine_dependency(self):
        page = self.client.get(self.add)
        for text in ("Basic information", "Pricing &amp; tax", "Opening stock", "Variants", "Description", "Barcode preview", "Pictures",
                     "Stock &amp; status", "Save &amp; add another", "Generate", "Sub-category", "This product has variants"):
            self.assertContains(page, text)
        self.assertNotContains(page, "productForm")
        self.assertNotContains(page, "@submit")
        self.assertContains(page, "js/product-form.js")
        self.assertContains(page, 'data-slot="selling_price"')
        self.assertNotContains(page, "Stock now")

    def test_edit_page_shows_stock_now_and_no_opening_stock(self):
        page = self.client.get(reverse("catalog:product_edit", args=[self.w.tea.product_id]))
        self.assertContains(page, "Stock now")
        self.assertContains(page, "Floor")
        self.assertNotContains(page, 'id="pf-opening"')
        self.assertContains(page, "Save changes")

    def test_new_row_starts_with_empty_prices(self):
        page = self.client.get(self.add).content.decode()
        row = re.search(r'<input[^>]*name="variants-0-selling_price"[^>]*>', page).group(0)
        self.assertNotIn("value=", row)

    def test_generate_returns_a_valid_unused_ean13(self):
        code = self.client.get(reverse("catalog:barcode_generate")).json()["code"]
        self.assertEqual(len(code), 13)
        digits = [int(c) for c in code]
        self.assertEqual((10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12])) % 10) % 10, digits[12])

    def test_generate_needs_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("catalog:barcode_generate")).status_code, 302)

    def test_sku_is_optional_and_made_from_the_name(self):
        post = product_post(variant_row(0), name="Green Tea 100", sku="", unit=self.w.unit.pk, stock_location=self.w.warehouse.pk)
        self.client.post(self.add, post)
        self.assertEqual(Product.objects.get(name="Green Tea 100").sku, "GREEN-TEA-100")
        post["name"] = "Green tea 100"                                         # same code is taken: -2
        self.client.post(self.add, {**post, **variant_row(0, barcode="")})
        self.assertEqual(Product.objects.get(name="Green tea 100").sku, "GREEN-TEA-100-2")

    def test_clearing_the_sku_of_a_saved_product_keeps_it(self):
        product = self.w.tea.product
        post = product_post(variant_row(0, selling_price="100", id=self.w.tea.pk, product=product.pk), name="Tea", sku="", unit=self.w.unit.pk)
        post["variants-INITIAL_FORMS"] = "1"
        self.client.post(reverse("catalog:product_edit", args=[product.pk]), post)
        product.refresh_from_db()
        self.assertEqual(product.sku, "TEA")

    def test_category_tree_is_sent_for_the_sub_category_picker(self):
        parent = Category.objects.create(store=self.w.store, name="Drinks")
        Category.objects.create(store=self.w.store, name="Tea", parent=parent)
        page = self.client.get(self.add)
        self.assertContains(page, 'id="pf-cats"')
        self.assertContains(page, '"parent_id": %d' % parent.pk)
