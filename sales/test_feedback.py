"""Customer feedback asked at the POS after payment, and the ERP screen that lists it."""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld, grant
from sales.models import Sale, SaleFeedback


class FeedbackTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.sale = Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, reference="S-1", biller=self.w.manager)
        self.client.login(username="manager", password=PASSWORD)
        self.url = reverse("sales:pos_feedback")

    def send(self, **body):
        body.setdefault("sale_id", self.sale.pk)
        return self.client.post(self.url, data=json.dumps(body), content_type="application/json")

    def test_rating_and_comment_are_saved(self):
        resp = self.send(rating=5, comment="  Very friendly staff  ")
        self.assertEqual(resp.json(), {"ok": True})
        fb = SaleFeedback.objects.get()
        self.assertEqual((fb.rating, fb.comment, fb.created_by), (5, "Very friendly staff", self.w.manager))

    def test_comment_is_optional(self):
        self.assertTrue(self.send(rating=3).json()["ok"])
        self.assertEqual(SaleFeedback.objects.get().comment, "")

    def test_sending_again_replaces_it(self):
        self.send(rating=2, comment="slow")
        self.send(rating=4, comment="better")
        self.assertEqual(SaleFeedback.objects.count(), 1)
        self.assertEqual(SaleFeedback.objects.get().rating, 4)

    def test_bad_ratings_are_refused(self):
        for bad in (0, 6, "x", None, -1):
            self.assertEqual(self.send(rating=bad).status_code, 400, bad)
        self.assertFalse(SaleFeedback.objects.exists())

    def test_sale_of_another_store_is_not_found(self):
        from tenants.models import Store, Warehouse
        other = Store.objects.create(name="Other", company=self.w.company)
        wh = Warehouse.objects.create(store=other, name="W", is_default=True)
        foreign = Sale.objects.create(store=other, warehouse=wh, reference="S-2", biller=self.w.manager)
        self.assertEqual(self.send(rating=5, sale_id=foreign.pk).status_code, 404)

    def test_get_is_not_allowed_and_permission_is_needed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.send(rating=5).status_code, 200)        # cashiers can add sales, so they can record feedback
        self.client.logout()
        from django.contrib.auth import get_user_model
        get_user_model().objects.create_user("nobody", "n@example.com", PASSWORD, store=self.w.store)
        self.client.login(username="nobody", password=PASSWORD)
        self.assertEqual(self.send(rating=5).status_code, 403)

    def test_list_page_shows_average_and_filters(self):
        self.send(rating=5, comment="Great")
        other = Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, reference="S-3", biller=self.w.manager)
        self.send(rating=1, comment="Bad queue", sale_id=other.pk)
        resp = self.client.get(reverse("sales:feedback_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "3.0")
        self.assertContains(resp, "Great")
        only_bad = self.client.get(reverse("sales:feedback_list"), {"rating": "1"})
        self.assertContains(only_bad, "Bad queue")
        self.assertNotContains(only_bad, "Great")
        self.assertContains(self.client.get(reverse("sales:feedback_list"), {"q": "S-3"}), "Bad queue")

    def test_empty_list_renders(self):
        resp = self.client.get(reverse("sales:feedback_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No feedback yet")

    def test_pos_page_has_the_feedback_hooks(self):
        from register import services
        session = services.open_session(self.w.counter1, self.w.manager, 0)
        s = self.client.session
        s["active_register_session_id"] = session.pk
        s.save()
        resp = self.client.get(reverse("dashboard:pos"))
        self.assertContains(resp, reverse("sales:pos_feedback"))
        self.assertContains(resp, "sendFeedback()")
