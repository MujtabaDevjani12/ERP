"""Customer feedback on one sale: the card on the sale page, the drawer to add or edit it, and the stars in the sale list."""
from django.urls import reverse

from core.pos_fixtures import grant
from sales.models import Sale, SaleFeedback
from sales.tests import PosBase

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class SaleFeedbackTests(PosBase):
    def setUp(self):
        super().setUp()
        grant(self.w.cashier, "sales.view_sale", "sales.add_sale")
        r = self.post("sales:pos_finalize", {"items": [self.line(self.w.milk, 1)], "payments": [{"method": "cash", "amount": "100"}]})
        self.sale = Sale.objects.get(pk=r.json()["sale_id"])

    def test_detail_shows_the_feedback_card_with_stars_and_comment(self):
        SaleFeedback.objects.create(sale=self.sale, rating=4, comment="Quick and friendly")
        resp = self.client.get(reverse("sales:sale_detail", args=[self.sale.pk]))
        self.assertContains(resp, "Customer feedback")
        self.assertContains(resp, "Quick and friendly")
        self.assertContains(resp, "4 out of 5 stars")
        self.assertContains(resp, "Edit feedback")

    def test_detail_without_feedback_offers_to_add_it(self):
        resp = self.client.get(reverse("sales:sale_detail", args=[self.sale.pk]))
        self.assertContains(resp, "has not rated this visit yet")
        self.assertContains(resp, "Add feedback")

    def test_drawer_saves_and_replaces_the_feedback(self):
        url = reverse("sales:sale_feedback", args=[self.sale.pk])
        self.assertEqual(self.client.get(url, **XHR).status_code, 200)
        r = self.client.post(url, {"rating": "5", "comment": " Great "}, **XHR)
        self.assertEqual(r.json()["ok"], True)
        fb = SaleFeedback.objects.get(sale=self.sale)
        self.assertEqual((fb.rating, fb.comment, fb.created_by.username), (5, "Great", "cashier"))
        self.client.post(url, {"rating": "2", "comment": ""}, **XHR)
        self.assertEqual(SaleFeedback.objects.filter(sale=self.sale).count(), 1)
        self.assertEqual(SaleFeedback.objects.get(sale=self.sale).rating, 2)

    def test_drawer_needs_a_rating(self):
        r = self.client.post(reverse("sales:sale_feedback", args=[self.sale.pk]), {"rating": "", "comment": "x"}, **XHR)
        self.assertContains(r, "Choose 1 to 5 stars")
        self.assertFalse(SaleFeedback.objects.exists())

    def test_sale_list_shows_the_rating(self):
        SaleFeedback.objects.create(sale=self.sale, rating=3)
        self.assertContains(self.client.get(reverse("sales:sale_list")), "3 out of 5 stars")


class SaleListFilterTests(PosBase):
    def setUp(self):
        super().setUp()
        grant(self.w.cashier, "sales.view_sale")
        paid = self.post("sales:pos_finalize", {"items": [self.line(self.w.milk, 1)], "payments": [{"method": "cash", "amount": "500"}]})
        self.paid = Sale.objects.get(pk=paid.json()["sale_id"])

    def refs(self, **params):
        resp = self.client.get(reverse("sales:sale_list"), params)
        return [s.reference for s in resp.context["sales"]]

    def test_payment_filter(self):
        self.assertIn(self.paid.reference, self.refs(pay="paid"))
        self.assertNotIn(self.paid.reference, self.refs(pay="due"))
        self.assertNotIn(self.paid.reference, self.refs(pay="partial"))

    def test_warehouse_filter(self):
        self.assertIn(self.paid.reference, self.refs(warehouse=self.paid.warehouse_id))
        self.assertEqual(self.refs(warehouse=self.paid.warehouse_id + 999), [])

    def test_exchange_link_is_offered(self):
        grant(self.w.cashier, "sales.add_salereturn")
        self.assertContains(self.client.get(reverse("sales:sale_detail", args=[self.paid.pk])), "?exchange=1")
