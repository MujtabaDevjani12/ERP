"""Gift cards: spending at the POS really lowers the balance, can never overspend, is all-or-nothing with the sale, and is fully recorded."""
import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.pos_fixtures import PASSWORD, grant
from customers import giftcards as gc
from customers.models import GiftCard, GiftCardTransaction
from payments.models import Payment
from sales.models import Sale
from sales.tests import PosBase
from tenants.models import Store


class GiftCardPosTests(PosBase):
    def setUp(self):
        super().setUp()
        self.card = GiftCard.objects.create(store=self.w.store, code="GC-100", initial_balance=Decimal("150"), current_balance=Decimal("150"))

    def pay(self, amount, code="gc-100", token=None, **extra):
        body = {"items": [self.line(self.w.tea, 2)], "payments": [{"method": "gift_card", "amount": str(amount), "reference_no": code}], **extra}
        if token:
            body["checkout_token"] = token
        return self.post("sales:pos_finalize", body)

    def test_payment_takes_the_money_off_the_card(self):
        r = self.pay("100")                                                      # tea x2 = 200; 100 on the card, the rest on credit needs a customer
        self.assertFalse(r.json()["ok"])                                         # part payment with no customer is refused...
        self.card.refresh_from_db(); self.assertEqual(self.card.current_balance, 150)    # ...and the card is untouched
        from customers.models import Customer
        cust = Customer.objects.create(store=self.w.store, name="Ali")
        r = self.pay("100", customer_id=cust.pk)
        self.assertTrue(r.json()["ok"], r.json())
        self.card.refresh_from_db(); self.assertEqual(self.card.current_balance, 50)
        sale = Sale.objects.get()
        pay = Payment.objects.get()
        self.assertEqual((pay.method, pay.reference_no, pay.amount), ("gift_card", "GC-100", 100))   # stored with the real card number, not what was typed
        tx = GiftCardTransaction.objects.get(kind="redeem")
        self.assertEqual((tx.amount, tx.balance_after, tx.sale, tx.user), (-100, 50, sale, self.w.cashier))

    def test_cannot_spend_more_than_the_balance(self):
        r = self.pay("200")
        data = r.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["code"], "gift_card_balance")
        self.assertEqual(Sale.objects.count(), 0)
        self.card.refresh_from_db(); self.assertEqual(self.card.current_balance, 150)

    def test_unknown_expired_off_and_other_branch_cards_are_refused(self):
        self.assertEqual(self.pay("100", code="NOPE").json()["error"], "No gift card with this number.")
        self.card.expires_on = timezone.localdate() - datetime.timedelta(days=1); self.card.save()
        self.assertIn("expired", self.pay("100").json()["error"])
        self.card.expires_on = None; self.card.is_active = False; self.card.save()
        self.assertIn("switched off", self.pay("100").json()["error"])
        other = Store.objects.create(name="Other", company=self.w.company)
        GiftCard.objects.create(store=other, code="GC-OTHER", initial_balance=500, current_balance=500)
        self.assertEqual(self.pay("200", code="GC-OTHER").json()["error"], "No gift card with this number.")
        self.assertEqual(Sale.objects.count(), 0)

    def test_gift_card_number_is_required(self):
        self.assertEqual(self.pay("100", code="").json()["error"], "Enter the gift card number.")

    def test_a_retry_with_the_same_token_does_not_charge_twice(self):
        from customers.models import Customer
        cust = Customer.objects.create(store=self.w.store, name="Ali")
        first = self.pay("150", customer_id=cust.pk, token="tok-1")
        again = self.pay("150", customer_id=cust.pk, token="tok-1")
        self.assertTrue(first.json()["ok"] and again.json()["ok"])
        self.assertEqual(again.json()["sale_id"], first.json()["sale_id"])
        self.card.refresh_from_db(); self.assertEqual(self.card.current_balance, 0)
        self.assertEqual(GiftCardTransaction.objects.filter(kind="redeem").count(), 1)

    def test_split_with_cash(self):
        r = self.post("sales:pos_finalize", {"items": [self.line(self.w.tea, 2)], "payments": [
            {"method": "gift_card", "amount": "150", "reference_no": "GC-100"}, {"method": "cash", "amount": "70"}]})
        self.assertTrue(r.json()["ok"], r.json())
        self.card.refresh_from_db(); self.assertEqual(self.card.current_balance, 0)
        self.assertEqual(Sale.objects.get().due_amount, 0)                                # tea x2 = 200 + 10% tax = 220

    def test_pay_the_balance_later_from_the_sale_page(self):
        from customers.models import Customer
        cust = Customer.objects.create(store=self.w.store, name="Ali")
        r = self.post("sales:pos_finalize", {"items": [self.line(self.w.tea, 2)], "customer_id": cust.pk,
                                             "payments": [{"method": "cash", "amount": "50"}]})
        sale = Sale.objects.get(pk=r.json()["sale_id"])
        self.client.logout(); self.client.login(username="manager", password=PASSWORD)
        self.client.post(reverse("sales:sale_add_payment", args=[sale.pk]), {"method": "gift_card", "amount": "100", "reference_no": "gc-100"})
        self.card.refresh_from_db(); self.assertEqual(self.card.current_balance, 50)
        self.assertEqual(Sale.objects.get(pk=sale.pk).due_amount, 70)                  # 220 - 50 cash - 100 card
        self.client.post(reverse("sales:sale_add_payment", args=[sale.pk]), {"method": "gift_card", "amount": "50", "reference_no": "NOPE"})
        self.assertEqual(Sale.objects.get(pk=sale.pk).due_amount, 70)                  # a bad card adds nothing
        self.card.refresh_from_db(); self.assertEqual(self.card.current_balance, 50)

    def test_lookup_endpoint(self):
        url = reverse("customers:giftcard_lookup")
        ok = self.client.get(url, {"code": "gc-100"}).json()
        self.assertEqual((ok["ok"], ok["code"], ok["balance"]), (True, "GC-100", "150.00"))
        self.assertFalse(self.client.get(url, {"code": "zzz"}).json()["ok"])


class GiftCardAdminTests(TestCase):
    def setUp(self):
        from core.pos_fixtures import PosWorld
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        self.ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def test_create_records_the_issue(self):
        r = self.client.post(reverse("customers:giftcard_add"), {"code": " gc-7 ", "initial_balance": "250", "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        card = GiftCard.objects.get()
        self.assertEqual((card.code, card.store, card.initial_balance, card.current_balance), ("GC-7", self.w.store, 250, 250))
        self.assertEqual(list(card.transactions.values_list("kind", "amount", "balance_after")), [("issue", 250, 250)])
        self.assertEqual(self.client.post(reverse("customers:giftcard_add"), {"code": "X", "initial_balance": "0"}).status_code, 200)   # value must be > 0

    def test_balance_cannot_be_edited_by_hand(self):
        card = GiftCard.objects.create(store=self.w.store, code="G1", initial_balance=100, current_balance=40)
        page = self.client.get(reverse("customers:giftcard_edit", args=[card.pk]))
        self.assertNotContains(page, "current_balance")
        self.client.post(reverse("customers:giftcard_edit", args=[card.pk]), {"code": "G1", "current_balance": "9999", "is_active": "on"})
        card.refresh_from_db(); self.assertEqual(card.current_balance, 40)

    def test_top_up(self):
        card = GiftCard.objects.create(store=self.w.store, code="G1", initial_balance=100, current_balance=40)
        url = reverse("customers:giftcard_topup", args=[card.pk])
        self.assertContains(self.client.get(url, **self.ajax), "Amount to add")
        r = self.client.post(url, {"amount": "60", "note": "cash"}, **self.ajax)
        self.assertTrue(r.json()["ok"])
        card.refresh_from_db(); self.assertEqual(card.current_balance, 100)
        tx = card.transactions.get()
        self.assertEqual((tx.kind, tx.amount, tx.balance_after, tx.note), ("topup", 60, 100, "cash"))
        for bad in ("0", "-5", "abc", "nan"):
            r = self.client.post(url, {"amount": bad}, **self.ajax)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["Content-Type"].split(";")[0], "text/html", bad)                                                # the form again, with an error
        card.refresh_from_db(); self.assertEqual(card.current_balance, 100)

    def test_list_history_scoping_and_permissions(self):
        card = GiftCard.objects.create(store=self.w.store, code="G1", initial_balance=100, current_balance=40)
        other = Store.objects.create(name="Other", company=self.w.company)
        theirs = GiftCard.objects.create(store=other, code="G2", initial_balance=1, current_balance=1)
        r = self.client.get(reverse("customers:giftcard_list"))
        self.assertContains(r, "G1"); self.assertNotContains(r, "G2")
        self.assertEqual(self.client.get(reverse("customers:giftcard_history", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("customers:giftcard_topup", args=[theirs.pk]), {"amount": "5"}, **self.ajax).status_code, 404)
        self.client.logout(); self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("customers:giftcard_list")).status_code, 403)
        self.assertEqual(self.client.post(reverse("customers:giftcard_topup", args=[card.pk]), {"amount": "5"}, **self.ajax).status_code, 403)
