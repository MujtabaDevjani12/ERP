"""Variant rules of the product form: unique names, blank price = same as the first variant, switching "has variants" off."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from catalog.models import Product
from catalog.tests import product_post, variant_row
from core.pos_fixtures import PASSWORD, PosWorld


class VariantRulesTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        self.add = reverse("catalog:product_add")

    def create(self, *rows, **kw):
        kw.setdefault("unit", self.w.unit.pk)
        kw.setdefault("stock_location", self.w.warehouse.pk)
        kw.setdefault("name", "Shirt")
        kw.setdefault("sku", "SHIRT")
        return self.client.post(self.add, product_post(*rows, **kw))

    def shirt(self):
        self.create(variant_row(0, name="Small", selling_price="10"), variant_row(1, name="Medium", selling_price="12"),
                    variant_row(2, name="Large", selling_price="14"), product_type="variant")
        return Product.objects.get(sku="SHIRT")

    def edit_post(self, product, **changes):
        rows = list(product.variants.order_by("name"))
        data = {"name": product.name, "sku": product.sku, "unit": product.unit_id, "product_type": product.product_type, "alert_quantity": "0",
                "is_active": "on", "variants-TOTAL_FORMS": str(len(rows)), "variants-INITIAL_FORMS": str(len(rows)),
                "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000"}
        for i, v in enumerate(rows):
            data.update({f"variants-{i}-id": v.pk, f"variants-{i}-product": product.pk, f"variants-{i}-name": v.name, f"variants-{i}-sku": v.sku,
                         f"variants-{i}-barcode": v.barcode, f"variants-{i}-cost_price": v.cost_price, f"variants-{i}-selling_price": v.selling_price,
                         f"variants-{i}-is_active": "on"})
        data.update(changes)
        return self.client.post(reverse("catalog:product_edit", args=[product.pk]), data)

    def test_blank_cost_and_price_on_a_new_row_follow_the_first_variant(self):
        self.create(variant_row(0, name="Small", cost_price="4", selling_price="10"),
                    variant_row(1, name="Medium", cost_price="", selling_price=""), product_type="variant")
        medium = Product.objects.get(sku="SHIRT").variants.get(name="Medium")
        self.assertEqual((medium.cost_price, medium.selling_price), (Decimal("4"), Decimal("10")))

    def test_a_product_with_no_selling_price_anywhere_is_refused(self):
        resp = self.create(variant_row(0, name="Small", selling_price=""), product_type="variant")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Product.objects.filter(sku="SHIRT").exists())

    def test_two_variants_cannot_share_a_name(self):
        resp = self.create(variant_row(0, name="Small"), variant_row(1, name="small"), product_type="variant")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already used by another variant")
        self.assertFalse(Product.objects.filter(sku="SHIRT").exists())

    def test_switching_variants_off_keeps_one_default_variant(self):
        product = self.shirt()
        self.assertEqual(product.variants.count(), 3)
        resp = self.edit_post(product, product_type="single")
        self.assertRedirects(resp, reverse("catalog:product_list"))
        product.refresh_from_db()
        self.assertEqual(product.product_type, "single")
        left = list(product.variants.all())
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0].name, "Default")

    def test_switching_variants_off_in_the_browser_ticks_the_extra_rows_for_removal(self):
        product = self.shirt()
        resp = self.edit_post(product, product_type="single", **{"variants-1-DELETE": "on", "variants-2-DELETE": "on"})
        self.assertRedirects(resp, reverse("catalog:product_list"))
        self.assertEqual(product.variants.count(), 1)

    def test_turning_a_variant_product_plain_renames_its_one_variant_default(self):
        product = self.shirt()
        self.edit_post(product, product_type="single", **{"variants-1-DELETE": "on", "variants-2-DELETE": "on"})
        self.assertEqual([v.name for v in product.variants.all()], ["Default"])

    def test_editing_a_variant_product_keeps_every_variant(self):
        product = self.shirt()
        resp = self.edit_post(product, **{"variants-0-selling_price": "99"})
        self.assertRedirects(resp, reverse("catalog:product_list"))
        self.assertEqual(product.variants.count(), 3)
        self.assertEqual(product.variants.get(name="Large").selling_price, Decimal("99"))

    def test_edit_page_has_the_removal_hooks(self):
        product = self.shirt()
        resp = self.client.get(reverse("catalog:product_edit", args=[product.pk]))
        self.assertContains(resp, "pv-gone-tag")
        self.assertContains(resp, "will be removed when you save")


class ProductListTests(VariantRulesTests):
    def test_list_shows_variant_count_brand_filter_and_bulk_print(self):
        from catalog.models import Brand
        product = self.shirt()
        brand = Brand.objects.create(store=product.store, name="Acme")
        product.brand = brand
        product.save()
        page = self.client.get(reverse("catalog:product_list"))
        self.assertContains(page, "3 variants")
        self.assertContains(page, 'name="brand"')
        self.assertContains(page, "data-pick")
        hit = self.client.get(reverse("catalog:product_list"), {"brand": brand.pk})
        self.assertContains(hit, "Shirt")
        miss = self.client.get(reverse("catalog:product_list"), {"brand": brand.pk + 999})
        self.assertNotContains(miss, "3 variants")
