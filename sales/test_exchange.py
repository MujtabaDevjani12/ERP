"""Exchange at the POS = a normal return, then straight back to the POS to ring up the new items."""
from django.urls import reverse

from core.pos_fixtures import PASSWORD, grant
from sales.models import Sale, SaleReturn
from sales.tests import PosBase


class ExchangeTests(PosBase):
    def setUp(self):
        super().setUp()
        grant(self.w.cashier, "sales.view_sale")
        r = self.post("sales:pos_finalize", {"items": [self.line(self.w.milk, 2)], "payments": [{"method": "cash", "amount": "100"}]})
        self.sale = Sale.objects.get(pk=r.json()["sale_id"])
        self.item = self.sale.items.get()
        self.url = reverse("sales:sale_return", args=[self.sale.pk])

    def test_return_page_in_exchange_mode(self):
        page = self.client.get(self.url + "?exchange=1")
        self.assertContains(page, "Exchange"); self.assertContains(page, 'name="exchange" value="1"')
        self.assertContains(page, 'value="Exchange"')                                   # the reason is pre-filled
        self.assertNotContains(self.client.get(self.url), 'name="exchange"')

    def test_exchange_return_goes_back_to_the_pos(self):
        r = self.client.post(self.url, {"exchange": "1", "reason": "Exchange", "refund_method": "cash", "restock": "on", f"qty_{self.item.pk}": "1"})
        ret = SaleReturn.objects.get()
        self.assertRedirects(r, reverse("dashboard:pos") + f"?exchange_return={ret.reference}", fetch_redirect_response=False)
        self.assertEqual(self.w.stock_of(self.w.milk), 4)                               # 5 - 2 sold + 1 back
        pos = self.client.get(reverse("dashboard:pos"), {"exchange_return": ret.reference})
        esc = lambda ref: ref.replace("-", "\\u002D")                                 # escapejs writes hyphens as \u002D inside the page's JS
        self.assertContains(pos, f'ret: "{esc(ret.reference)}"'); self.assertContains(pos, f'sale: "{esc(self.sale.reference)}"')
        self.assertContains(pos, 'refund: "50.00"')

    def test_a_normal_return_still_goes_to_the_sale_page(self):
        r = self.client.post(self.url, {"reason": "damaged", "refund_method": "cash", f"qty_{self.item.pk}": "1"})
        self.assertRedirects(r, reverse("sales:sale_detail", args=[self.sale.pk]), fetch_redirect_response=False)

    def test_unknown_or_foreign_reference_shows_no_banner(self):
        pos = self.client.get(reverse("dashboard:pos"), {"exchange_return": "RET-NOPE"})
        self.assertContains(pos, "exchange: null")

    def test_pos_shows_the_exchange_button_only_with_permission(self):
        self.assertContains(self.client.get(reverse("dashboard:pos")), "Exchange items")
        self.w.cashier.user_permissions.remove(*self.w.cashier.user_permissions.filter(codename="add_salereturn"))
        self.client.logout(); self.client.login(username="cashier", password=PASSWORD)
        s = self.client.session; s["active_register_session_id"] = self.session.pk; s.save()
        self.assertNotContains(self.client.get(reverse("dashboard:pos")), "Exchange items")
