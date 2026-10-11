"""Customer deposits: advance money kept on a prepaid card of its own, spendable at the POS."""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld, grant
from customers import giftcards as gc
from customers.models import Customer, GiftCard, GiftCardTransaction
from tenants.models import Store

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class DepositTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.cust = Customer.objects.create(store=self.w.store, name="Zara Khan", phone="0300")
        self.client.login(username="manager", password=PASSWORD)
        self.url = reverse("customers:customer_deposit", args=[self.cust.pk])

    def test_first_deposit_creates_the_card_and_later_ones_top_it_up(self):
        r = self.client.post(self.url, {"amount": "500", "note": "advance"}, **AJAX)
        self.assertEqual(r.json()["ok"], True)
        card = GiftCard.objects.get(code=f"DEP-{self.cust.pk}")
        self.assertEqual((card.customer, card.store, card.current_balance), (self.cust, self.w.store, 500))
        self.client.post(self.url, {"amount": "250.50"}, **AJAX)
        card.refresh_from_db()
        self.assertEqual(card.current_balance, Decimal("750.50"))
        self.assertEqual(list(card.transactions.order_by("id").values_list("kind", "amount")), [("issue", 500), ("topup", Decimal("250.50"))])

    def test_bad_amounts_are_refused(self):
        for bad in ("", "0", "-5", "abc", "nan"):
            r = self.client.post(self.url, {"amount": bad}, **AJAX)
            self.assertEqual(r.headers["content-type"].split(";")[0], "text/html", bad)
        self.assertFalse(GiftCard.objects.exists())

    def test_spend_the_deposit_at_the_pos_lookup(self):
        self.assertFalse(self.client.get(reverse("customers:giftcard_lookup"), {"customer": self.cust.pk}).json()["ok"])   # nothing deposited yet
        gc.add_deposit(self.cust, 300, user=self.w.manager)
        d = self.client.get(reverse("customers:giftcard_lookup"), {"customer": self.cust.pk}).json()
        self.assertEqual((d["ok"], d["balance"], d["code"]), (True, "300.00", f"DEP-{self.cust.pk}"))
        gc.redeem(self.w.store, d["code"], 120)
        self.assertEqual(GiftCard.objects.get().current_balance, 180)

    def test_deposit_is_scoped_to_the_branch_and_to_permissions(self):
        other = Store.objects.create(name="Other", company=self.w.company)
        theirs = Customer.objects.create(store=other, name="Not ours")
        self.assertEqual(self.client.get(reverse("customers:customer_deposit", args=[theirs.pk]), **AJAX).status_code, 404)
        gc.add_deposit(theirs, 100)
        self.assertFalse(self.client.get(reverse("customers:giftcard_lookup"), {"customer": theirs.pk}).json()["ok"])
        self.client.logout(); self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.post(self.url, {"amount": "5"}, **AJAX).status_code, 403)

    def test_hand_made_cards_cannot_take_the_reserved_prefix(self):
        r = self.client.post(reverse("customers:giftcard_add"), {"code": "dep-99", "initial_balance": "10"})
        self.assertContains(r, "reserved for customer deposits")
        self.assertFalse(GiftCard.objects.exists())

    def test_customer_list_shows_the_deposit(self):
        gc.add_deposit(self.cust, 420)
        page = self.client.get(reverse("customers:customer_list"))
        self.assertContains(page, "420.00"); self.assertContains(page, "Add deposit")
