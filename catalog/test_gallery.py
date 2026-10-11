"""Product form: cover + gallery pictures (count, size, real images, removal), barcode preview, and the new page structure."""
import io
import tempfile
from unittest import mock
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from catalog.models import Product, ProductImage
from catalog.tests import product_post, variant_row
from core.pos_fixtures import PASSWORD, PosWorld, grant

MEDIA = tempfile.mkdtemp(prefix="pos-media-")


def png(name="p.png", size=(8, 8), pad=0):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    data = buf.getvalue() + b"\0" * pad
    return SimpleUploadedFile(name, data, content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA)
class GalleryTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        self.url = reverse("catalog:product_add")

    def create(self, files=(), cover=None, **kw):
        data = product_post(variant_row(0), unit=self.w.unit.pk, stock_location=self.w.warehouse.pk, **kw)
        if files:
            data["gallery"] = list(files)
        if cover is not None:
            data["image"] = cover
        return self.client.post(self.url, data)

    def edit(self, product, files=(), **extra):
        v = product.variants.get()
        data = {"name": product.name, "sku": product.sku, "product_type": "single", "unit": product.unit_id, "alert_quantity": "0", "is_active": "on",
                "variants-TOTAL_FORMS": "1", "variants-INITIAL_FORMS": "1", "variants-MIN_NUM_FORMS": "0", "variants-MAX_NUM_FORMS": "1000",
                "variants-0-id": v.pk, "variants-0-product": product.pk, "variants-0-name": v.name, "variants-0-sku": v.sku, "variants-0-barcode": v.barcode,
                "variants-0-cost_price": "10", "variants-0-selling_price": "25", "variants-0-is_active": "on"}
        data.update(extra)
        if files:
            data["gallery"] = list(files)
        return self.client.post(reverse("catalog:product_edit", args=[product.pk]), data)

    def test_cover_and_gallery_pictures_are_saved(self):
        r = self.create(files=[png("a.png"), png("b.png")], cover=png("cover.png"))
        self.assertRedirects(r, reverse("catalog:product_list"))
        p = Product.objects.get(name="Rice")
        self.assertTrue(p.image)
        self.assertEqual(p.gallery.count(), 2)
        self.assertEqual([g.sort_order for g in p.gallery.all()], [1, 2])

    def test_at_most_eight_gallery_pictures_counting_the_ones_kept(self):
        self.create(files=[png(f"{i}.png") for i in range(8)])
        p = Product.objects.get(name="Rice")
        self.assertEqual(p.gallery.count(), 8)
        r = self.edit(p, files=[png("nine.png")])
        self.assertContains(r, "at most 8 gallery pictures")
        self.assertEqual(p.gallery.count(), 8)
        gone = list(p.gallery.values_list("pk", flat=True)[:2])
        r = self.edit(p, files=[png("n1.png"), png("n2.png")], remove_gallery=gone)         # frees two places, adds two
        self.assertRedirects(r, reverse("catalog:product_list"))
        self.assertEqual(p.gallery.count(), 8)
        self.assertFalse(ProductImage.objects.filter(pk__in=gone).exists())

    def test_big_or_fake_pictures_are_refused_and_nothing_is_saved(self):
        r = self.create(files=[png("big.png", pad=3 * 1024 * 1024 + 10)])
        self.assertContains(r, "larger than 3 MB")
        r = self.create(files=[SimpleUploadedFile("evil.png", b"not an image at all", content_type="image/png")])
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Product.objects.filter(name="Rice").exists())
        r = self.create(cover=png("bigcover.png", pad=3 * 1024 * 1024 + 10))
        self.assertContains(r, "larger than 3 MB")

    def test_cover_can_be_removed_but_not_when_a_new_one_is_sent(self):
        self.create(cover=png("c.png"))
        p = Product.objects.get(name="Rice")
        self.edit(p, remove_image="on")
        p.refresh_from_db()
        self.assertFalse(p.image)
        self.edit(p, files=(), image=png("new.png"))
        p.refresh_from_db()
        self.assertTrue(p.image)
        self.edit(p, remove_image="on", image=png("newer.png"))
        p.refresh_from_db()
        self.assertTrue(p.image)                                           # a new upload wins over "remove"

    def test_cannot_remove_another_products_picture(self):
        other = Product.objects.create(store=self.w.store, name="Other", sku="OTH", unit=self.w.unit)
        pic = ProductImage.objects.create(product=other, image=png("o.png"))
        self.create()
        p = Product.objects.get(name="Rice")
        r = self.edit(p, remove_gallery=[pic.pk])
        self.assertEqual(r.status_code, 200)                              # invalid choice: refused
        self.assertTrue(ProductImage.objects.filter(pk=pic.pk).exists())

    def test_edit_page_shows_the_gallery_and_stock_column(self):
        self.create(files=[png("a.png")], cover=png("c.png"))
        p = Product.objects.get(name="Rice")
        page = self.client.get(reverse("catalog:product_edit", args=[p.pk]))
        self.assertContains(page, "Gallery"); self.assertContains(page, "remove_gallery"); self.assertContains(page, "In stock")
        self.assertContains(page, "Barcode preview"); self.assertContains(page, "This product has variants")
        add = self.client.get(self.url)
        self.assertContains(add, "Opening stock"); self.assertContains(add, "Opening qty"); self.assertContains(add, "Quick add")

    def test_in_stock_shows_the_total_on_hand(self):
        v = self.w.tea
        page = self.client.get(reverse("catalog:product_edit", args=[v.product_id]))
        self.assertContains(page, 'class="pv-onhand"')
        self.assertContains(page, ">10<")                                  # the fixture holds 10 tea

    def test_barcode_preview_endpoint(self):
        def fake_png(code, symbology="code128"):                                               # python-barcode is not installed in every test environment
            if not code.isascii():
                raise ValueError("Code128 cannot encode this")
            return b"\x89PNG fake"
        with mock.patch("core.utils.generate_barcode_png", side_effect=fake_png):
            self._barcode_checks()

    def _barcode_checks(self):
        ok = self.client.get(reverse("catalog:barcode_preview"), {"code": "ABC-123"})
        self.assertEqual((ok.status_code, ok["content-type"]), (200, "image/png"))
        self.assertEqual(self.client.get(reverse("catalog:barcode_preview"), {"code": ""}).status_code, 400)
        self.assertEqual(self.client.get(reverse("catalog:barcode_preview"), {"code": "x" * 65}).status_code, 400)
        self.assertEqual(self.client.get(reverse("catalog:barcode_preview"), {"code": "ünï©ode"}).status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("catalog:barcode_preview"), {"code": "A"}).status_code, 302)
