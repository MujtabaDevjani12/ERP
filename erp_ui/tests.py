from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from company.models import Company
from masters.models import PaymentTerm
from tenants.models import Store


class ErpScreenTests(TestCase):
    """The custom ERP screens drive the existing ModelAdmin rules: forms, permissions, search, CSV."""

    def setUp(self):
        User = get_user_model()
        self.company = Company.objects.create(name="Acme")
        self.store = Store.objects.create(name="Main", company=self.company)
        self.boss = User.objects.create_superuser("boss", "b@example.com", "pw-12345-x")
        self.plain = User.objects.create_user("plain", "p@example.com", "pw-12345-x")
        self.term = PaymentTerm.objects.create(company=self.company, name="Net 30", days=30)
        self.client.force_login(self.boss)

    def url(self, name, *extra, model="paymentterm"):
        return reverse(f"erp_ui:{name}", args=["masters", model, *extra])

    def test_module_overview_and_sidebar(self):
        resp = self.client.get(reverse("erp_ui:module", args=["masters"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Payment terms")
        self.assertContains(resp, "Master Data")

    def test_list_search_filter_and_csv(self):
        PaymentTerm.objects.create(company=self.company, name="Cash", days=0)
        resp = self.client.get(self.url("list"))
        self.assertContains(resp, "Net 30")
        self.assertContains(resp, "Cash")
        resp = self.client.get(self.url("list"), {"f_is_active": "1"})
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get(self.url("list"), {"o": "-days"})
        self.assertEqual(resp.status_code, 200)
        csv = self.client.get(self.url("list"), {"export": "csv"})
        self.assertEqual(csv["Content-Type"].split(";")[0], "text/csv")
        self.assertIn("Net 30", csv.content.decode("utf-8"))

    def test_add_edit_delete_cycle(self):
        self.assertEqual(self.client.get(self.url("add")).status_code, 200)
        resp = self.client.post(self.url("add"), {"company": self.company.pk, "name": "Net 45", "days": 45, "is_active": "on"})
        self.assertEqual(resp.status_code, 302)
        term = PaymentTerm.objects.get(name="Net 45")

        self.assertEqual(self.client.get(self.url("edit", term.pk)).status_code, 200)
        resp = self.client.post(self.url("edit", term.pk), {"company": self.company.pk, "name": "Net 45", "days": 50, "is_active": "on"})
        self.assertEqual(resp.status_code, 302)
        term.refresh_from_db()
        self.assertEqual(term.days, 50)

        self.assertEqual(self.client.get(self.url("delete", term.pk)).status_code, 200)
        self.assertEqual(self.client.post(self.url("delete", term.pk)).status_code, 302)
        self.assertFalse(PaymentTerm.objects.filter(pk=term.pk).exists())

    def test_invalid_form_is_redisplayed_not_saved(self):
        resp = self.client.post(self.url("add"), {"company": self.company.pk, "name": "", "days": "x"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(PaymentTerm.objects.filter(days=0, name="").exists())
        self.assertContains(resp, "This field is required")

    def test_unknown_screen_is_404(self):
        self.assertEqual(self.client.get(reverse("erp_ui:list", args=["masters", "nothing"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("erp_ui:list", args=["sales", "sale"])).status_code, 404)   # POS apps are not ERP screens

    def test_user_without_permission_is_refused(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(self.url("list")).status_code, 403)
        self.assertEqual(self.client.get(self.url("add")).status_code, 403)
        self.assertEqual(self.client.post(self.url("delete", self.term.pk)).status_code, 403)

    def test_lookup_returns_matches_and_checks_permission(self):
        url = reverse("erp_ui:lookup", args=["company", "company"]) + "?for=masters.paymentterm"
        resp = self.client.get(url, {"q": "Ac"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([r["text"] for r in resp.json()["results"]], ["Acme"])
        # a model the form has no relation to cannot be browsed through this endpoint
        bad = reverse("erp_ui:lookup", args=["tenants", "store"]) + "?for=masters.paymentterm"
        self.assertEqual(self.client.get(bad).status_code, 403)
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_bulk_action_without_selection_does_not_crash(self):
        resp = self.client.post(self.url("list"), {"action": "anything"})
        self.assertEqual(resp.status_code, 302)

    def test_row_preview_fragment(self):
        resp = self.client.get(self.url("preview", self.term.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Net 30")
        self.assertNotContains(resp, "app-sidebar")            # a fragment for the drawer, not a full page
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(self.url("preview", self.term.pk)).status_code, 403)

    def test_quick_create_drawer_returns_a_fragment_then_json(self):
        resp = self.client.get(self.url("add") + "?_drawer=1")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "app-sidebar")
        resp = self.client.post(self.url("add") + "?_drawer=1",
                                {"_drawer": "1", "company": self.company.pk, "name": "Net 90", "days": 90, "is_active": "on"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["text"], PaymentTerm.objects.get(name="Net 90").name)
        bad = self.client.post(self.url("add") + "?_drawer=1", {"_drawer": "1", "company": self.company.pk, "name": "", "days": "x"})
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "This field is required")
        self.assertFalse(PaymentTerm.objects.filter(days=0, name="").exists())
