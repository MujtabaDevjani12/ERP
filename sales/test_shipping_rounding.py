"""Shipping charge and total rounding at the POS: priced on the server, stored on the sale, kept through hold/resume, shown on the invoice."""
from decimal import Decimal

from django.urls import reverse

from configuration.models import POSConfiguration
from customers.models import Customer
from sales.models import Sale
from sales.pricing import round_total
from sales.tests import PosBase


class RoundingMathTests(PosBase):
    def test_round_total_modes(self):
        d = Decimal
        self.assertEqual(round_total(d("102.40"), "none"), (d("102.40"), 0))
        self.assertEqual(round_total(d("102.40"), "nearest_1"), (d("102.00"), d("-0.40")))
        self.assertEqual(round_total(d("102.50"), "nearest_1"), (d("103.00"), d("0.50")))
        self.assertEqual(round_total(d("102.40"), "nearest_5"), (d("100.00"), d("-2.40")))
        self.assertEqual(round_total(d("103.00"), "nearest_5"), (d("105.00"), d("2.00")))
        self.assertEqual(round_total(d("104.00"), "nearest_10"), (d("100.00"), d("-4.00")))
        self.assertEqual(round_total(d("105.00"), "nearest_10"), (d("110.00"), d("5.00")))
        self.assertEqual(round_total(d("102.01"), "up"), (d("103.00"), d("0.99")))
        self.assertEqual(round_total(d("102.99"), "down"), (d("102.00"), d("-0.99")))
        self.assertEqual(round_total(d("102.00"), "up"), (d("102.00"), 0))


class ShippingRoundingTests(PosBase):
    def quote(self, **extra):
        return self.post("sales:pos_quote", {"items": [self.line(self.w.milk, 1)], **extra}).json()

    def set_rounding(self, mode):
        POSConfiguration.objects.update_or_create(store=self.w.store, defaults={"rounding": mode})

    def test_shipping_is_added_after_tax_and_discount(self):
        q = self.quote(shipping="30")
        self.assertEqual((q["subtotal"], q["tax"], q["shipping"], q["rounding"], q["total"]), ("50.00", "0.00", "30.00", "0.00", "80.00"))
        q = self.post("sales:pos_quote", {"items": [self.line(self.w.tea, 1)], "shipping": "25", "order_discount": {"type": "percent", "value": "10"}}).json()
        self.assertEqual(q["total"], "124.00")                  # tea 100 - 10% = 90 + 10% tax 9 = 99, + 25 shipping

    def test_bad_shipping_is_refused(self):
        for bad in ("-5", "abc", "nan", "99999999999"):
            r = self.post("sales:pos_quote", {"items": [self.line(self.w.milk, 1)], "shipping": bad})
            self.assertEqual(r.status_code, 400, bad)

    def test_rounding_follows_the_branch_setting(self):
        self.set_rounding("nearest_10")
        q = self.quote(shipping="3")                            # 53 -> 50
        self.assertEqual((q["total"], q["rounding"]), ("50.00", "-3.00"))
        self.set_rounding("none")
        self.assertEqual(self.quote(shipping="3")["total"], "53.00")

    def test_checkout_stores_shipping_and_rounding_and_the_sale_balances(self):
        self.set_rounding("nearest_5")
        cust = Customer.objects.create(store=self.w.store, name="Ali")
        r = self.post("sales:pos_finalize", {"items": [self.line(self.w.milk, 1)], "shipping": "12", "customer_id": cust.pk,
                                             "payments": [{"method": "cash", "amount": "60"}]})
        self.assertTrue(r.json()["ok"], r.json())
        sale = Sale.objects.get()
        self.assertEqual((sale.subtotal, sale.shipping_amount, sale.rounding_amount, sale.total_amount), (50, 12, -2, 60))      # 62 -> 60
        self.assertEqual(sale.subtotal - sale.discount_amount + sale.tax_amount + sale.shipping_amount + sale.rounding_amount, sale.total_amount)
        self.assertEqual(sale.due_amount, 0)
        page = self.client.get(reverse("sales:invoice_print", args=[sale.pk]))
        self.assertContains(page, "Shipping"); self.assertContains(page, "Rounding")

    def test_cart_keeps_shipping_through_hold_and_resume(self):
        cart = {"items": [{"variant_id": self.w.milk.pk, "name": "Milk", "unit_price": "50", "quantity": "1", "stock": "5"}], "shipping": "15"}
        self.assertTrue(self.post("sales:pos_cart_save", {"cart": cart}).json()["ok"])
        self.assertEqual(self.client.get(reverse("sales:pos_cart_get")).json()["cart"]["shipping"], "15.00")
        held = self.post("sales:pos_finalize", {"items": [self.line(self.w.milk, 1)], "shipping": "15", "hold": True}).json()
        self.assertTrue(held["ok"], held)
        self.assertEqual(Sale.objects.get(pk=held["sale_id"]).shipping_amount, 15)
        self.post("sales:pos_cart_clear", {})
        back = self.client.post(reverse("sales:resume_held_sale", args=[held["sale_id"]]), content_type="application/json").json()
        self.assertEqual(back["cart"]["shipping"], "15.00")

    def test_plain_sale_is_unchanged(self):
        r = self.post("sales:pos_finalize", {"items": [self.line(self.w.milk, 1)], "payments": [{"method": "cash", "amount": "50"}]})
        sale = Sale.objects.get(pk=r.json()["sale_id"])
        self.assertEqual((sale.shipping_amount, sale.rounding_amount, sale.total_amount), (0, 0, 50))
