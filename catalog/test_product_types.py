"""Product types (standard / service / combo / digital), promotion price and barcode type - form rules and how the POS treats them."""
import json
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from catalog import barcodes
from catalog.models import ComboItem, Product, ProductVariant
from catalog.tests import product_post, variant_row
from inventory.models import StockItem
from sales.models import Sale
from sales.tests import PosBase


class BarcodeRulesTests(PosBase):
    def test_check_digit_and_validation(self):
        self.assertEqual(barcodes.check_digit("400638133393"), "1")                  # 4006381333931 is a real EAN-13
        self.assertIsNone(barcodes.validate("ean13", "4006381333931"))
        self.assertIsNone(barcodes.validate("ean13", "400638133393"))                # 12 digits: the check digit is added when drawn
        self.assertIn("wrong", barcodes.validate("ean13", "4006381333932"))
        self.assertIn("12 or 13 digits", barcodes.validate("ean13", "ABC"))
        self.assertIsNone(barcodes.validate("ean8", "96385074"))
        self.assertIn("7 or 8", barcodes.validate("ean8", "12"))
        self.assertIsNone(barcodes.validate("upca", "036000291452"))
        self.assertIn("cannot hold", barcodes.validate("code39", "abc#"))
        self.assertIsNone(barcodes.validate("code39", "abc-12"))                     # lower case is upper-cased
        self.assertIsNone(barcodes.validate("code128", "anything 123!"))
        self.assertIsNone(barcodes.validate("ean13", ""))

    def test_generated_numeric_codes_are_valid(self):
        for kind in ("ean13", "ean8", "upca"):
            code = barcodes.generate(kind)
            self.assertIsNone(barcodes.validate(kind, code))
            self.assertEqual(len(code), barcodes.DIGITS[kind] + 1)

    def test_generate_endpoint_follows_the_chosen_type(self):
        for kind, size in (("ean8", 8), ("upca", 12), ("ean13", 13), ("code128", 13)):
            code = self.client.get(reverse("catalog:barcode_generate"), {"type": kind}).json()["code"]
            self.assertEqual(len(code), size)


class ProductTypeFormTests(PosBase):
    def setUp(self):
        super().setUp()
        self.client.login(username="manager", password="pw-12345-x")
        self.add = reverse("catalog:product_add")

    def create(self, *rows, **kw):
        kw.setdefault("unit", self.w.unit.pk)
        kw.setdefault("stock_location", self.w.warehouse.pk)
        kw.setdefault("name", "Thing")
        kw.setdefault("sku", "THING")
        return self.client.post(self.add, product_post(*(rows or (variant_row(0),)), **kw))

    def combo_json(self, *pairs):
        return json.dumps([{"variant": v.pk, "qty": str(q)} for v, q in pairs])

    def test_page_shows_type_picker_promotion_barcode_type_and_combo_box(self):
        html = self.client.get(self.add).content.decode()
        for needle in ('name="item_type"', 'value="service"', 'value="combo"', 'value="digital"', 'name="barcode_symbology"',
                       'name="promo_price"', 'name="promo_start"', 'id="pf-combo"', 'name="combo_items"'):
            self.assertIn(needle, html)
        self.assertEqual(self.client.get(reverse("catalog:product_edit", args=[self.w.tea.product_id])).status_code, 200)

    def test_service_is_saved_without_any_stock(self):
        resp = self.create(variant_row(0, opening_stock="9"), item_type="service", name="Haircut", sku="CUT")
        self.assertEqual(resp.status_code, 302, resp.content[:400])
        product = Product.objects.get(sku="CUT")
        self.assertEqual(product.item_type, "service")
        self.assertFalse(product.tracks_stock)
        self.assertFalse(StockItem.objects.filter(variant__product=product).exists())     # an opening quantity makes no sense for a service

    def test_digital_item_is_saved(self):
        self.create(item_type="digital", name="E-book", sku="EBOOK")
        self.assertEqual(Product.objects.get(sku="EBOOK").item_type, "digital")

    def test_old_forms_that_do_not_send_the_new_fields_still_save_as_standard(self):
        self.create(name="Plain", sku="PLAIN")
        product = Product.objects.get(sku="PLAIN")
        self.assertEqual((product.item_type, product.barcode_symbology, product.promo_price), ("standard", "code128", None))

    def test_combo_needs_contents_and_saves_its_parts(self):
        resp = self.create(item_type="combo", name="Tea set", sku="SET")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Add at least one product to the combo.")
        self.assertFalse(Product.objects.filter(sku="SET").exists())
        resp = self.create(item_type="combo", name="Tea set", sku="SET", combo_items=self.combo_json((self.w.tea, 2), (self.w.milk, "0.5")))
        self.assertEqual(resp.status_code, 302, resp.content[:400])
        parts = {ci.component_id: ci.quantity for ci in Product.objects.get(sku="SET").combo_items.all()}
        self.assertEqual(parts, {self.w.tea.pk: Decimal("2.000"), self.w.milk.pk: Decimal("0.500")})

    def test_editing_a_combo_replaces_parts_and_leaving_combo_clears_them(self):
        self.create(item_type="combo", name="Tea set", sku="SET", combo_items=self.combo_json((self.w.tea, 2), (self.w.milk, 1)))
        combo = Product.objects.get(sku="SET")
        variant = combo.variants.get()
        edit = lambda **kw: self.client.post(reverse("catalog:product_edit", args=[combo.pk]), {
            "name": combo.name, "sku": combo.sku, "unit": combo.unit_id, "product_type": "single", "alert_quantity": "0", "is_active": "on",
            "variants-TOTAL_FORMS": "1", "variants-INITIAL_FORMS": "1", "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000",
            "variants-0-id": variant.pk, "variants-0-product": combo.pk, "variants-0-name": "Default", "variants-0-sku": variant.sku,
            "variants-0-barcode": variant.barcode, "variants-0-cost_price": "10", "variants-0-selling_price": "25", "variants-0-is_active": "on", **kw})
        self.assertEqual(edit(item_type="combo", combo_items=self.combo_json((self.w.tea, 3))).status_code, 302)
        self.assertEqual({ci.component_id: ci.quantity for ci in combo.combo_items.all()}, {self.w.tea.pk: Decimal("3.000")})
        self.assertEqual(edit(item_type="standard").status_code, 302)
        self.assertEqual(combo.combo_items.count(), 0)

    def test_combo_rules(self):
        service = self.create(item_type="service", name="Fitting", sku="FIT") and Product.objects.get(sku="FIT").variants.get()
        resp = self.create(item_type="combo", name="Bad", sku="BAD", combo_items=self.combo_json((service, 1)))
        self.assertContains(resp, "is not available")                                                    # a service cannot be a part
        resp = self.create(item_type="combo", name="Bad", sku="BAD", combo_items=self.combo_json((self.w.tea, 0)))
        self.assertContains(resp, "quantity above zero")
        resp = self.create(item_type="combo", name="Bad", sku="BAD", combo_items=self.combo_json((self.w.tea, 1), (self.w.tea, 1)))
        self.assertContains(resp, "listed twice")
        resp = self.create(variant_row(0, name="S"), variant_row(1, name="M"), product_type="variant", item_type="combo", name="Bad", sku="BAD",
                           combo_items=self.combo_json((self.w.tea, 1)))
        self.assertContains(resp, "combo is one product")
        resp = self.create(item_type="combo", name="Bad", sku="BAD", combo_items="not json")
        self.assertContains(resp, "could not be read")
        self.assertFalse(Product.objects.filter(sku="BAD").exists())

    def test_a_product_used_inside_a_combo_cannot_become_a_service(self):
        self.create(item_type="combo", name="Tea set", sku="SET", combo_items=self.combo_json((self.w.tea, 1)))
        tea = self.w.tea.product
        resp = self.client.post(reverse("catalog:product_edit", args=[tea.pk]), {
            "name": tea.name, "sku": tea.sku, "unit": tea.unit_id, "product_type": "single", "alert_quantity": "0", "is_active": "on", "item_type": "service",
            "variants-TOTAL_FORMS": "1", "variants-INITIAL_FORMS": "1", "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000",
            "variants-0-id": self.w.tea.pk, "variants-0-product": tea.pk, "variants-0-name": "Default", "variants-0-sku": self.w.tea.sku,
            "variants-0-barcode": self.w.tea.barcode, "variants-0-cost_price": "10", "variants-0-selling_price": "100", "variants-0-is_active": "on"})
        self.assertContains(resp, "part of the combo")

    def test_promotion_is_saved_and_validated(self):
        today = timezone.localdate()
        resp = self.create(variant_row(0, selling_price="100"), name="Sale item", sku="SALE", promo_price="80",
                           promo_start=str(today), promo_end=str(today + timedelta(days=3)))
        self.assertEqual(resp.status_code, 302, resp.content[:400])
        product = Product.objects.get(sku="SALE")
        self.assertEqual((product.promo_price, product.promo_end), (Decimal("80.00"), today + timedelta(days=3)))
        self.assertContains(self.create(variant_row(0, selling_price="100"), name="X", sku="X1", promo_price="100"), "must be lower than the selling price")
        self.assertContains(self.create(variant_row(0, selling_price="100"), name="X", sku="X2", promo_price="50", promo_start="2026-05-02", promo_end="2026-05-01"),
                            "cannot end before it starts")
        self.assertContains(self.create(variant_row(0, selling_price="100"), name="X", sku="X3", promo_start="2026-05-02"), "Enter the promotion price")
        self.assertContains(self.create(variant_row(0, name="S"), variant_row(1, name="M"), product_type="variant", name="X", sku="X4", promo_price="5"),
                            "single product")
        self.assertFalse(Product.objects.filter(sku__in=["X1", "X2", "X3", "X4"]).exists())

    def test_barcode_must_fit_the_chosen_type(self):
        resp = self.create(variant_row(0, barcode="HELLO"), barcode_symbology="ean13", name="E", sku="E1")
        self.assertContains(resp, "EAN-13 needs 12 or 13 digits")
        self.assertFalse(Product.objects.filter(sku="E1").exists())
        resp = self.create(variant_row(0, barcode="400638133393"), barcode_symbology="ean13", name="E", sku="E2")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Product.objects.get(sku="E2").barcode_symbology, "ean13")

    def test_empty_barcode_on_a_numeric_type_gets_a_real_valid_code(self):
        self.create(variant_row(0, barcode=""), barcode_symbology="ean13", name="E", sku="E3")
        code = Product.objects.get(sku="E3").variants.get().barcode
        self.assertIsNone(barcodes.validate("ean13", code))
        self.assertEqual(len(code), 13)
        self.create(variant_row(0, barcode=""), barcode_symbology="ean8", name="E", sku="E4")
        self.assertEqual(len(Product.objects.get(sku="E4").variants.get().barcode), 8)

    def test_label_data_carries_the_barcode_type_and_sale_price(self):
        product = self.w.tea.product
        product.barcode_symbology, product.promo_price = "ean13", Decimal("90")
        product.save()
        item = self.client.get(reverse("catalog:barcode_items"), {"variant": self.w.tea.pk}).json()["results"][0]
        self.assertEqual((item["symbology"], item["price"]), ("ean13", "90.00"))


class PromotionPriceTests(PosBase):
    def promo(self, **kw):
        product = self.w.milk.product
        for k, v in kw.items():
            setattr(product, k, v)
        product.save()
        return ProductVariant.objects.get(pk=self.w.milk.pk)

    def test_current_price_follows_the_promotion_window(self):
        today = timezone.localdate()
        self.assertEqual(self.promo().current_price, Decimal("50.00"))                                   # no promotion
        self.assertEqual(self.promo(promo_price=Decimal("40")).current_price, Decimal("40"))             # open ended
        self.assertEqual(self.promo(promo_start=today + timedelta(days=1)).current_price, Decimal("50.00"))   # not started
        self.assertEqual(self.promo(promo_start=today, promo_end=today).current_price, Decimal("40"))    # last day counts
        self.assertEqual(self.promo(promo_start=None, promo_end=today - timedelta(days=1)).current_price, Decimal("50.00"))  # over
        self.assertEqual(self.promo(promo_price=Decimal("60"), promo_end=None).current_price, Decimal("50.00"))   # never above the normal price
        self.assertFalse(self.promo(promo_price=None).on_promotion)

    def test_pos_lookup_and_checkout_use_the_promotion_price(self):
        self.promo(promo_price=Decimal("40"), promo_start=None, promo_end=None)
        row = self.client.get(reverse("catalog:api_variant_lookup"), {"pos": "1", "q": "BCMILK"}).json()["results"][0]
        self.assertEqual((row["price"], row["regular_price"], row["on_promo"]), ("40.00", "50.00", True))
        data = self.checkout([self.line(self.w.milk, 2)], [{"method": "cash", "amount": "80"}]).json()
        sale = Sale.objects.get(pk=data["sale_id"])
        self.assertEqual((sale.items.get().unit_price, sale.total_amount), (Decimal("40.00"), Decimal("80.00")))

    def test_an_expired_promotion_is_not_used(self):
        self.promo(promo_price=Decimal("40"), promo_end=timezone.localdate() - timedelta(days=1))
        data = self.checkout([self.line(self.w.milk, 1)], [{"method": "cash", "amount": "50"}]).json()
        self.assertEqual(Sale.objects.get(pk=data["sale_id"]).total_amount, Decimal("50.00"))


class NonStockItemsAtThePosTests(PosBase):
    def setUp(self):
        super().setUp()
        self.service = self.make("Haircut", "CUT", Product.ITEM_SERVICE, "200")
        self.digital = self.make("E-book", "EBOOK", Product.ITEM_DIGITAL, "30")
        self.combo = self.make("Tea set", "SET", Product.ITEM_COMBO, "260")
        ComboItem.objects.create(combo=self.combo.product, component=self.w.tea, quantity=Decimal("2"))
        ComboItem.objects.create(combo=self.combo.product, component=self.w.milk, quantity=Decimal("1"))

    def make(self, name, sku, kind, price):
        product = Product(store=self.w.store, unit=self.w.unit, name=name, sku=sku, item_type=kind)
        product._skip_default_variant = True
        product.save()
        return ProductVariant.objects.create(product=product, sku=f"{sku}-1", barcode=f"BC{sku}", selling_price=Decimal(price), cost_price=Decimal("10"))

    def lookup(self, **params):
        return {r["name"]: r for r in self.client.get(reverse("catalog:api_variant_lookup"), {"pos": "1", **params}).json()["results"]}

    def test_services_and_digital_items_are_always_listed_without_stock(self):
        rows = self.lookup()
        for name in ("Haircut", "E-book"):
            self.assertIn(name, rows)
            self.assertFalse(rows[name]["tracked"])
            self.assertEqual(Decimal(rows[name]["stock"]), Decimal("999999"))
        self.assertEqual(rows["Haircut"]["item_type"], "service")
        self.assertTrue(rows["Tea"]["tracked"])

    def test_a_combo_is_offered_while_every_part_is_on_the_shelf(self):
        rows = self.lookup()
        self.assertEqual(Decimal(rows["Tea set"]["stock"]), Decimal("5"))              # tea 10/2 = 5, milk 5/1 = 5
        StockItem.objects.filter(variant=self.w.milk).update(quantity=Decimal("3"))
        self.assertEqual(Decimal(self.lookup()["Tea set"]["stock"]), Decimal("3"))
        StockItem.objects.filter(variant=self.w.tea).update(quantity=Decimal("1"))      # one tea is not enough for a set (needs 2)
        self.assertNotIn("Tea set", self.lookup())

    def test_selling_a_service_takes_no_stock_and_needs_no_stock_row(self):
        resp = self.checkout([self.line(self.service, 2), self.line(self.digital, 1)], [{"method": "cash", "amount": "430"}])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Sale.objects.get().total_amount, Decimal("430.00"))
        self.assertFalse(StockItem.objects.filter(variant__in=[self.service, self.digital]).exists())

    def test_selling_a_combo_takes_its_parts_off_the_shelf(self):
        resp = self.checkout([self.line(self.combo, 2)], [{"method": "cash", "amount": "520"}])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.w.stock_of(self.w.tea), Decimal("6"))                    # 10 - 2 sets x 2
        self.assertEqual(self.w.stock_of(self.w.milk), Decimal("3"))                   # 5 - 2 sets x 1
        self.assertFalse(StockItem.objects.filter(variant=self.combo).exists())        # the combo itself is never stocked

    def test_a_combo_cannot_be_sold_when_a_part_is_short_and_nothing_is_saved(self):
        StockItem.objects.filter(variant=self.w.milk).update(quantity=Decimal("1"))
        resp = self.checkout([self.line(self.combo, 2)], [{"method": "cash", "amount": "520"}])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "stock_changed")
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(self.w.stock_of(self.w.tea), Decimal("10"))                   # the tea taken before the failure was rolled back
        StockItem.objects.filter(variant=self.w.milk).update(quantity=Decimal("0"))
        resp = self.checkout([self.line(self.combo, 1)], [{"method": "cash", "amount": "260"}])
        self.assertEqual(resp.json()["code"], "out_of_stock")

    def test_the_stock_check_and_cart_revalidation_understand_the_types(self):
        body = {"items": [{"variant_id": self.service.pk, "quantity": "50"}, {"variant_id": self.combo.pk, "quantity": "9"}]}
        out = self.post("sales:pos_stock_check", body).json()
        self.assertEqual([p["variant_id"] for p in out["problems"]], [self.combo.pk])   # 9 sets > 5 possible; the service is fine

    def test_returning_a_combo_puts_its_parts_back_and_a_service_touches_no_stock(self):
        data = self.checkout([self.line(self.combo, 1), self.line(self.service, 1)], [{"method": "cash", "amount": "460"}]).json()
        sale = Sale.objects.get(pk=data["sale_id"])
        self.assertEqual((self.w.stock_of(self.w.tea), self.w.stock_of(self.w.milk)), (Decimal("8"), Decimal("4")))
        post = {"reason": "changed mind", "refund_method": "cash", "restock": "on"}
        post.update({f"qty_{item.pk}": "1" for item in sale.items.all()})
        resp = self.client.post(reverse("sales:sale_return", args=[sale.pk]), post)
        self.assertEqual(resp.status_code, 302, resp.content[:500])
        self.assertEqual((self.w.stock_of(self.w.tea), self.w.stock_of(self.w.milk)), (Decimal("10"), Decimal("5")))
        self.assertFalse(StockItem.objects.filter(variant__in=[self.service, self.combo]).exists())

    def test_low_stock_screens_ignore_services(self):
        product = self.service.product
        product.alert_quantity = Decimal("5")
        product.save()
        StockItem.objects.create(variant=self.service, warehouse=self.w.warehouse, quantity=Decimal("0"))      # left over from when it was a standard product
        page = self.client.get(reverse("inventory:stock_list"), {"low": "1"}).content.decode()
        self.assertNotIn("Haircut", page)
