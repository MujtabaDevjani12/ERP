import json

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from company.models import Company
from tenants.models import Store


def render(source, **ctx):
    return Template("{% load ui %}" + source).render(Context(ctx))


class UiKitTagTests(SimpleTestCase):
    """The shared UI kit: icons, status pills, slot components, sparklines."""

    def test_icon_renders_svg_and_falls_back_for_unknown_names(self):
        self.assertIn("<svg", render('{% icon "search" "w-5 h-5" %}'))
        self.assertIn('class="w-5 h-5"', render('{% icon "search" "w-5 h-5" %}'))
        self.assertIn("<path", render('{% icon "no-such-icon" %}'))

    def test_badge_picks_a_tone_from_the_status(self):
        self.assertIn("ui-badge-success", render('{% badge "paid" %}'))
        self.assertIn("ui-badge-warning", render('{% badge "pending" %}'))
        self.assertIn("ui-badge-danger", render('{% badge "cancelled" %}'))
        self.assertIn("ui-badge-neutral", render('{% badge "something_else" %}'))
        self.assertIn("Partially paid", render('{{ s|status_badge }}', s="partially_paid"))

    def test_component_with_body(self):
        html = render('{% component "card" title="Orders" %}<p id="inner">hello</p>{% endcomponent %}')
        self.assertIn("ui-card", html)
        self.assertIn("Orders", html)
        self.assertIn('<p id="inner">hello</p>', html)

    def test_component_without_body(self):
        html = render('{% ui "empty_state" title="No orders" text="Nothing yet" %}')
        self.assertIn("No orders", html)
        self.assertIn("ui-empty", html)

    def test_sparkline(self):
        self.assertIn("<svg", render("{% sparkline values %}", values=[1, 3, 2, 5]))
        self.assertEqual(render("{% sparkline values %}", values=[1]).strip(), "")


class AppShellTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.company = Company.objects.create(name="Acme")
        self.store = Store.objects.create(name="Main", company=self.company)
        self.boss = User.objects.create_superuser("boss", "b@example.com", "pw-12345-x")
        self.client.force_login(self.boss)

    def test_shell_renders_sidebar_topbar_and_command_data(self):
        resp = self.client.get(reverse("erp_ui:module", args=["masters"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "app-sidebar")
        self.assertContains(resp, "app-topbar")
        self.assertContains(resp, 'id="ui-commands"')
        self.assertContains(resp, "Master data")

    def test_global_search_requires_login_and_ignores_short_queries(self):
        url = reverse("erp_ui:search")
        self.assertEqual(self.client.get(url, {"q": "a"}).json(), {"results": []})
        resp = self.client.get(url, {"q": "Acme"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("results", resp.json())
        self.client.logout()
        self.assertEqual(self.client.get(url, {"q": "Acme"}).status_code, 302)

    def test_command_palette_data_is_valid_json(self):
        resp = self.client.get(reverse("erp_ui:module", args=["masters"]))
        html = resp.content.decode()
        start = html.index('id="ui-commands"')
        payload = html[html.index(">", start) + 1:html.index("</script>", start)]
        commands = json.loads(payload)
        self.assertTrue(any(c["kind"] == "nav" for c in commands))
