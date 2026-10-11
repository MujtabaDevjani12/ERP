"""Add/Edit product form and the barcode print page."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Product, ProductVariant
from core.pos_fixtures import PASSWORD, PosWorld, grant
from inventory.models import StockItem


def variant_row(i, **kw):
    row = {"name": "Default", "sku": "", "barcode": "", "cost_price": "10", "selling_price": "25", "opening_stock": "", "is_active": "on"}
    row.update(kw)
    return {f"variants-{i}-{k}": v for k, v in row.items()}


def product_post(*rows, **kw):
    data = {"name": "Rice", "sku": "RICE", "product_type": "single", "unit": "", "alert_quantity": "0", "is_active": "on",
            "variants-TOTAL_FORMS": str(len(rows)), "variants-INITIAL_FORMS": "0", "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000"}
    data.update(kw)
    for r in rows:
        data.update(r)
    return data


class ProductFormTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        self.url = reverse("catalog:product_add")

    def post(self, *rows, **kw):
        kw.setdefault("unit", self.w.unit.pk)
        kw.setdefault("stock_location", self.w.warehouse.pk)
        return self.client.post(self.url, product_post(*rows, **kw))

    def test_form_page_renders_for_add_and_edit(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.get(reverse("catalog:product_edit", args=[self.w.tea.product_id])).status_code, 200)

    def test_single_product_gets_exactly_one_variant_with_generated_sku_and_barcode(self):
        resp = self.post(variant_row(0))
        self.assertRedirects(resp, reverse("catalog:product_list"))
        product = Product.objects.get(name="Rice")
        self.assertEqual(product.variants.count(), 1)
        v = product.variants.get()
        self.assertTrue(v.sku and v.barcode)
        self.assertEqual(v.selling_price, Decimal("25"))

    def test_opening_stock_is_booked_so_the_product_can_be_sold(self):
        self.post(variant_row(0, opening_stock="12"))
        v = Product.objects.get(name="Rice").variants.get()
        self.assertEqual(self.w.stock_of(v), Decimal("12"))

    def test_without_opening_stock_no_stock_row_quantity(self):
        self.post(variant_row(0))
        v = Product.objects.get(name="Rice").variants.get()
        self.assertEqual(StockItem.objects.filter(variant=v, quantity__gt=0).count(), 0)

    def test_invalid_post_shows_errors_instead_of_crashing(self):
        resp = self.post(variant_row(0, selling_price="abc"), name="")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Product.objects.filter(sku="RICE").exists())
        self.assertContains(resp, "not saved")

    def test_duplicate_product_sku_is_refused(self):
        resp = self.post(variant_row(0), sku="TEA")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already uses this SKU")

    def test_duplicate_variant_barcode_is_refused(self):
        resp = self.post(variant_row(0, barcode="BCTEA"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already belongs to another variant")
        self.assertFalse(Product.objects.filter(name="Rice").exists())

    def test_manual_barcode_is_kept(self):
        self.post(variant_row(0, barcode="8964000111222"))
        self.assertEqual(Product.objects.get(name="Rice").variants.get().barcode, "8964000111222")

    def test_single_product_with_two_rows_keeps_only_the_first_variant(self):
        resp = self.post(variant_row(0), variant_row(1, name="Large"))
        self.assertRedirects(resp, reverse("catalog:product_list"))
        self.assertEqual(Product.objects.get(name="Rice").variants.count(), 1)

    def test_variant_product_with_two_variants(self):
        resp = self.post(variant_row(0, name="Small", selling_price="10"), variant_row(1, name="Large", selling_price="20"), product_type="variant")
        self.assertRedirects(resp, reverse("catalog:product_list"))
        product = Product.objects.get(name="Rice")
        self.assertEqual(product.variants.count(), 2)
        self.assertEqual(len({v.sku for v in product.variants.all()}), 2)

    def test_two_variants_with_same_barcode_in_one_form_are_refused(self):
        resp = self.post(variant_row(0, name="S", barcode="DUP1"), variant_row(1, name="L", barcode="DUP1"), product_type="variant")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Rice").exists())

    def test_save_and_print_goes_to_the_barcode_page(self):
        data = product_post(variant_row(0), unit=self.w.unit.pk, stock_location=self.w.warehouse.pk, save_and_print="1")
        resp = self.client.post(self.url, data)
        product = Product.objects.get(name="Rice")
        self.assertRedirects(resp, f"{reverse('catalog:barcode_print')}?product={product.pk}")

    def test_save_and_add_another(self):
        data = product_post(variant_row(0), unit=self.w.unit.pk, stock_location=self.w.warehouse.pk, save_and_new="1")
        self.assertRedirects(self.client.post(self.url, data), self.url)

    def test_editing_keeps_one_variant_and_can_add_another(self):
        self.post(variant_row(0))
        product = Product.objects.get(name="Rice")
        v = product.variants.get()
        data = {"name": "Rice", "sku": "RICE", "product_type": "variant", "unit": self.w.unit.pk, "alert_quantity": "0", "is_active": "on",
                "variants-TOTAL_FORMS": "2", "variants-INITIAL_FORMS": "1", "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000",
                "variants-0-id": v.pk, "variants-0-product": product.pk, "variants-0-name": "Small", "variants-0-sku": v.sku, "variants-0-barcode": v.barcode,
                "variants-0-cost_price": "10", "variants-0-selling_price": "25", "variants-0-is_active": "on"}
        data.update(variant_row(1, name="Large", selling_price="40"))
        resp = self.client.post(reverse("catalog:product_edit", args=[product.pk]), data)
        self.assertRedirects(resp, reverse("catalog:product_list"))
        self.assertEqual(sorted(product.variants.values_list("name", flat=True)), ["Large", "Small"])

    def test_deleting_a_variant_that_was_sold_switches_it_off_instead_of_crashing(self):
        product = self.w.tea.product
        extra = ProductVariant.objects.create(product=product, name="Big", sku="TEA-BIG", selling_price=Decimal("150"))
        from sales.models import Sale, SaleItem
        sale = Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, reference="T-1", biller=self.w.manager)
        SaleItem.objects.create(sale=sale, variant=extra, quantity=1, unit_price=Decimal("150"))
        data = {"name": "Tea", "sku": "TEA", "product_type": "variant", "unit": self.w.unit.pk, "alert_quantity": "0", "is_active": "on",
                "variants-TOTAL_FORMS": "2", "variants-INITIAL_FORMS": "2", "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000",
                "variants-0-id": self.w.tea.pk, "variants-0-product": product.pk, "variants-0-name": "Default", "variants-0-sku": self.w.tea.sku,
                "variants-0-barcode": self.w.tea.barcode, "variants-0-cost_price": "10", "variants-0-selling_price": "100", "variants-0-is_active": "on",
                "variants-1-id": extra.pk, "variants-1-product": product.pk, "variants-1-name": "Big", "variants-1-sku": "TEA-BIG",
                "variants-1-barcode": extra.barcode, "variants-1-cost_price": "0", "variants-1-selling_price": "150", "variants-1-DELETE": "on"}
        resp = self.client.post(reverse("catalog:product_edit", args=[product.pk]), data)
        self.assertEqual(resp.status_code, 302)
        extra.refresh_from_db()
        self.assertFalse(extra.is_active)


class BarcodePageTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)

    def api(self, **params):
        resp = self.client.get(reverse("catalog:barcode_items"), params)
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_page_renders_empty_and_preselected(self):
        self.assertEqual(self.client.get(reverse("catalog:barcode_print")).status_code, 200)
        resp = self.client.get(reverse("catalog:barcode_print"), {"product": self.w.tea.product_id})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "BCTEA")

    def test_search_by_name_sku_and_barcode(self):
        self.assertEqual([r["barcode"] for r in self.api(q="tea")["results"]], ["BCTEA"])
        self.assertEqual([r["barcode"] for r in self.api(q="MILK-1")["results"]], ["BCMILK"])
        self.assertEqual([r["sku"] for r in self.api(q="BCMILK")["results"]], ["MILK-1"])

    def test_no_query_returns_nothing(self):
        self.assertEqual(self.api()["results"], [])

    def test_category_filter_and_all(self):
        cat = Category.objects.create(store=self.w.store, name="Drinks")
        Product.objects.filter(pk=self.w.tea.product_id).update(category=cat)
        self.assertEqual([r["product"] for r in self.api(category=cat.pk)["results"]], ["Tea"])
        self.assertEqual(len(self.api(all=1)["results"]), 3)

    def test_out_of_stock_products_are_still_listed_for_labels(self):
        self.assertIn("Empty shelf", [r["product"] for r in self.api(q="empty")["results"]])

    def test_item_fields(self):
        r = self.api(product=self.w.milk.product_id)["results"][0]
        self.assertEqual((r["product"], r["variant"], r["price"], r["stock"]), ("Milk", "", "50.00", "5"))
        self.assertEqual(r["barcode"], "BCMILK")

    def test_requires_login_and_permission(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("catalog:barcode_print")).status_code, 302)
        self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("catalog:barcode_print")).status_code, 403)
        grant(self.w.cashier, "catalog.view_product")
        self.assertEqual(self.client.get(reverse("catalog:barcode_print")).status_code, 200)

    def test_store_isolation(self):
        from tenants.models import Store
        other = Store.objects.create(name="Other", company=self.w.company)
        unit = self.w.unit.__class__.objects.create(store=other, name="Box", short_name="bx")
        p = Product.objects.create(store=other, unit=unit, name="Secret", sku="SEC")
        self.assertEqual(self.api(q="Secret")["results"], [])
        self.assertEqual(self.api(product=p.pk)["results"], [])


class ScanLookupTests(TestCase):
    """The POS looks a scanned code up on its own: exact barcode or SKU, never a fuzzy list."""

    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)

    def lookup(self, **params):
        resp = self.client.get(reverse("catalog:api_variant_lookup"), params)
        self.assertEqual(resp.status_code, 200)
        return resp.json()["results"]

    def test_exact_barcode_returns_only_that_variant(self):
        self.assertEqual([r["barcode"] for r in self.lookup(q="BCTEA", exact="1")], ["BCTEA"])

    def test_exact_sku_and_case_insensitive(self):
        self.assertEqual([r["sku"] for r in self.lookup(q="milk-1", exact="1")], ["MILK-1"])

    def test_exact_does_not_match_fragments(self):
        self.assertEqual(self.lookup(q="BCTE", exact="1"), [])
        self.assertTrue(self.lookup(q="BCTE"))                       # the normal search still finds fragments

    def test_pos_page_contains_the_scan_hooks(self):
        from register import services
        session = services.open_session(self.w.counter1, self.w.manager, 0)
        s = self.client.session
        s["active_register_session_id"] = session.pk
        s.save()
        resp = self.client.get(reverse("dashboard:pos"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "onSearchInput()")
        self.assertContains(resp, "pos-tools")          # the colour-coded tool buttons (hold, resume, discount, return, more)
        self.assertContains(resp, "pos-statusbar")


class EditFormAndAttributeTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)

    def make_attrs(self):
        from catalog.models import ProductAttribute, ProductAttributeValue
        size = ProductAttribute.objects.create(store=self.w.store, name="Size")
        color = ProductAttribute.objects.create(store=self.w.store, name="Color")
        vals = {n: ProductAttributeValue.objects.create(attribute=a, value=v) for n, a, v in
                [("S", size, "Small"), ("M", size, "Medium"), ("R", color, "Red")]}
        return vals

    def test_editing_a_single_product_shows_only_its_own_variant_no_blank_extra(self):
        resp = self.client.get(reverse("catalog:product_edit", args=[self.w.tea.product_id]))
        self.assertEqual(len(resp.context["formset"].forms), 1)

    def test_new_product_starts_with_exactly_one_variant_row(self):
        resp = self.client.get(reverse("catalog:product_add"))
        self.assertEqual(len(resp.context["formset"].forms), 1)

    def test_editing_a_product_with_two_variants_shows_two(self):
        ProductVariant.objects.create(product=self.w.tea.product, name="Big", sku="TEA-BIG", selling_price=Decimal("150"))
        Product.objects.filter(pk=self.w.tea.product_id).update(product_type="variant")
        resp = self.client.get(reverse("catalog:product_edit", args=[self.w.tea.product_id]))
        self.assertEqual(len(resp.context["formset"].forms), 2)

    def test_saving_an_edited_single_product_keeps_one_variant(self):
        product, v = self.w.tea.product, self.w.tea
        data = {"name": "Tea", "sku": "TEA", "product_type": "single", "unit": self.w.unit.pk, "alert_quantity": "0", "is_active": "on",
                "variants-TOTAL_FORMS": "1", "variants-INITIAL_FORMS": "1", "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000",
                "variants-0-id": v.pk, "variants-0-product": product.pk, "variants-0-name": "Default", "variants-0-sku": v.sku, "variants-0-barcode": v.barcode,
                "variants-0-cost_price": "10", "variants-0-selling_price": "120", "variants-0-is_active": "on"}
        self.assertEqual(self.client.post(reverse("catalog:product_edit", args=[product.pk]), data).status_code, 302)
        self.assertEqual(product.variants.count(), 1)
        v.refresh_from_db()
        self.assertEqual(v.selling_price, Decimal("120"))

    def test_attributes_are_grouped_on_the_variant_card(self):
        self.make_attrs()
        form = self.client.get(reverse("catalog:product_add")).context["formset"].forms[0]
        groups = form.attribute_groups()
        self.assertEqual([(g["name"], [o["label"] for o in g["options"]]) for g in groups],
                         [("Color", ["Red"]), ("Size", ["Medium", "Small"])])

    def test_one_value_per_attribute_is_enforced(self):
        vals = self.make_attrs()
        bad = self.client_post_variant(attribute_values=[vals["S"].pk, vals["M"].pk])
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "Choose only one Size")
        self.assertFalse(Product.objects.filter(name="Rice").exists())

    def test_valid_attribute_choice_is_saved(self):
        vals = self.make_attrs()
        resp = self.client_post_variant(attribute_values=[vals["M"].pk, vals["R"].pk])
        self.assertEqual(resp.status_code, 302)
        v = Product.objects.get(name="Rice").variants.get()
        self.assertEqual({a.value for a in v.attribute_values.all()}, {"Medium", "Red"})

    def client_post_variant(self, **row_kw):
        data = product_post(variant_row(0, name="Medium / Red"), unit=self.w.unit.pk, stock_location=self.w.warehouse.pk, product_type="variant")
        data["variants-0-attribute_values"] = row_kw["attribute_values"]
        return self.client.post(reverse("catalog:product_add"), data)

    def test_attribute_page_adds_values_in_bulk_and_skips_repeats(self):
        from catalog.models import ProductAttribute
        resp = self.client.post(reverse("catalog:attribute_list"), {"name": "Size", "values": "Small, Medium\nLarge; small"})
        self.assertRedirects(resp, reverse("catalog:attribute_list"))
        attr = ProductAttribute.objects.get(name="Size")
        self.assertEqual(sorted(attr.values.values_list("value", flat=True)), ["Large", "Medium", "Small"])
        self.client.post(reverse("catalog:attribute_values", args=[attr.pk]), {"bulk": "Large, XL"})
        self.assertEqual(attr.values.count(), 4)

    def test_duplicate_attribute_name_is_refused_and_pages_render(self):
        self.client.post(reverse("catalog:attribute_list"), {"name": "Size", "values": ""})
        resp = self.client.post(reverse("catalog:attribute_list"), {"name": "size", "values": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already exists")
        from catalog.models import ProductAttribute
        attr = ProductAttribute.objects.get()
        self.assertEqual(self.client.get(reverse("catalog:attribute_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("catalog:attribute_values", args=[attr.pk])).status_code, 200)


class PosQuickViewLookupTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        self.big = ProductVariant.objects.create(product=self.w.tea.product, name="Large", sku="TEA-L", barcode="BCTEAL", selling_price=Decimal("150"))
        StockItem.objects.create(variant=self.big, warehouse=self.w.warehouse, quantity=Decimal("4"))

    def lookup(self, **p):
        return self.client.get(reverse("catalog:api_variant_lookup"), p).json()["results"]

    def test_results_carry_the_details_the_quick_view_shows(self):
        r = self.lookup(q="BCTEAL", exact="1")[0]
        self.assertEqual((r["product"], r["variant"], r["product_id"], r["sku"], r["barcode"]), ("Tea", "Large", self.w.tea.product_id, "TEA-L", "BCTEAL"))
        for key in ("category", "brand", "description", "attrs", "image", "stock", "price", "unit"):
            self.assertIn(key, r)

    def test_default_variant_has_no_variant_label(self):
        self.assertEqual(self.lookup(q="BCTEA", exact="1")[0]["variant"], "")

    def test_product_parameter_returns_all_variants_of_that_product(self):
        names = sorted(r["sku"] for r in self.lookup(product=self.w.tea.product_id))
        self.assertEqual(names, ["TEA-1", "TEA-L"])

    def test_pos_page_has_the_quick_view(self):
        from register import services
        session = services.open_session(self.w.counter1, self.w.manager, 0)
        s = self.client.session
        s["active_register_session_id"] = session.pk
        s.save()
        html = self.client.get(reverse("dashboard:pos")).content.decode()
        self.assertIn("pos-quick", html)
        self.assertIn("quickStep", html)
        self.assertIn("detailsFor(it)", html)
        self.assertIn("Keep open", html)
