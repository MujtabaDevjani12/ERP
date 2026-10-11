"""Extra currencies (Settings > Currencies) and their use on the barcode page."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from configuration.models import Currency
from core.pos_fixtures import PASSWORD, PosWorld


class CurrencyTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)

    def add(self, **kw):
        data = {"code": "usd", "name": "US Dollar", "symbol": "$", "rate": "280", "decimals": "2", "is_active": "on"}
        data.update(kw)
        return self.client.post(reverse("configuration:currency_add"), data)

    def test_list_page_shows_base_currency_even_when_empty(self):
        resp = self.client.get(reverse("configuration:currency_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Base")
        self.assertContains(resp, "PKR")

    def test_add_currency_uppercases_code_and_scopes_to_store(self):
        self.assertRedirects(self.add(), reverse("configuration:currency_list"))
        c = Currency.objects.get()
        self.assertEqual((c.code, c.store_id, c.rate), ("USD", self.w.store.pk, Decimal("280")))

    def test_base_currency_cannot_be_added_again(self):
        resp = self.add(code="PKR")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "already your main currency")
        self.assertFalse(Currency.objects.exists())

    def test_duplicate_and_bad_rate_are_refused(self):
        self.add()
        self.assertContains(self.add(), "already in the list")
        self.assertContains(self.add(code="AED", rate="0"), "more than zero")
        self.assertEqual(Currency.objects.count(), 1)

    def test_edit_and_delete(self):
        self.add()
        c = Currency.objects.get()
        resp = self.client.post(reverse("configuration:currency_edit", args=[c.pk]),
                                {"code": "USD", "name": "Dollar", "symbol": "US$", "rate": "300", "decimals": "2", "is_active": "on"})
        self.assertEqual(resp.status_code, 302)
        c.refresh_from_db()
        self.assertEqual((c.symbol, c.rate), ("US$", Decimal("300")))
        self.client.post(reverse("configuration:currency_delete", args=[c.pk]))
        self.assertFalse(Currency.objects.exists())

    def test_convert(self):
        self.add()
        self.assertEqual(Currency.objects.get().convert(Decimal("1400")), Decimal("5.00"))

    def test_active_currencies_reach_the_barcode_page_and_inactive_do_not(self):
        self.add()
        self.add(code="AED", symbol="AED", rate="76", is_active="")
        resp = self.client.get(reverse("catalog:barcode_print"))
        self.assertContains(resp, '"code": "USD"')
        self.assertNotContains(resp, '"code": "AED"')
        self.assertContains(resp, '"code": "PKR"')

    def test_other_stores_currencies_are_not_visible(self):
        from tenants.models import Store
        other = Store.objects.create(name="Other", company=self.w.company)
        Currency.objects.create(store=other, code="GBP", symbol="£", rate=Decimal("350"))
        self.assertNotContains(self.client.get(reverse("configuration:currency_list")), "GBP")
