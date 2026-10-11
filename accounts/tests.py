from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld


class BillerFilterTests(TestCase):
    def test_biller_filter(self):
        PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        url = reverse("accounts:staff_user_list")
        names = lambda qs: sorted(u.username for u in self.client.get(url, qs).context["staff_users"])
        self.assertEqual(names({"biller": "1"}), ["cashier", "cashier2"])
        self.assertEqual(names({"biller": "0"}), ["manager"])
        self.assertEqual(names({}), ["cashier", "cashier2", "manager"])
