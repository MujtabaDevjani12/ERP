"""Text size, text colour, currency position - and the money tag that makes the Currency settings real."""
from decimal import Decimal

from django.template import Context, Template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from configuration.models import SystemConfiguration
from core.pos_fixtures import PASSWORD, PosWorld
from core.templatetags.ui import format_money


class FormatMoneyTests(SimpleTestCase):
    def test_before_after_and_separators(self):
        self.assertEqual(format_money(Decimal("1234567.5"), "Rs"), "Rs 1,234,567.50")
        self.assertEqual(format_money(1234.5, "Rs", position="after"), "1,234.50 Rs")
        self.assertEqual(format_money(1234.5, "€", decimal_sep=",", thousands_sep="."), "€ 1.234,50")
        self.assertEqual(format_money(1234.5, "Rs", decimals=0), "Rs 1,235")
        self.assertEqual(format_money(1234.5, "Rs", show_symbol=False), "1,234.50")
        self.assertEqual(format_money(-5, "Rs"), "-Rs 5.00")
        self.assertEqual(format_money(None, "Rs"), "Rs 0.00")
        self.assertEqual(format_money("abc", "Rs"), "abc")


class DisplaySettingsTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        self.url = reverse("configuration:hub_section", args=["menu-fonts"])

    def post(self, **kw):
        data = {"secondary_color": "#123456", "font_family": "Inter", "font_size": "normal", "text_color": ""}
        data.update(kw)
        return self.client.post(self.url, data)

    def test_text_size_and_colour_are_saved_and_applied(self):
        self.assertRedirects(self.post(font_size="large", text_color="#1f2937"), self.url)
        cfg = SystemConfiguration.get()
        self.assertEqual((cfg.font_size, cfg.text_color), ("large", "#1f2937"))
        page = self.client.get(reverse("dashboard:home"))
        self.assertContains(page, "font-size: 108.75%")
        self.assertContains(page, "--ui-text: #1f2937")

    def test_small_text_and_default_colour(self):
        self.post(font_size="small")
        page = self.client.get(reverse("dashboard:home"))
        self.assertContains(page, "font-size: 93.75%")
        self.assertNotContains(page, "--ui-text: #")

    def test_a_colour_too_light_to_read_is_refused(self):
        r = self.post(text_color="#eeeeee")
        self.assertContains(r, "too light")
        r = self.post(text_color="red")
        self.assertContains(r, "must be a colour")
        self.assertEqual(SystemConfiguration.get().text_color, "")

    def test_clearing_the_colour_restores_the_default(self):
        self.post(text_color="#1f2937"); self.post(text_color="")
        self.assertEqual(SystemConfiguration.get().text_color, "")

    def test_old_form_post_without_the_new_fields_still_saves(self):
        r = self.client.post(self.url, {"secondary_color": "#123456", "font_family": "Poppins"})
        self.assertRedirects(r, self.url)
        self.assertEqual(SystemConfiguration.get().font_size, "normal")

    def test_currency_position_changes_how_amounts_are_written(self):
        cur = reverse("configuration:hub_section", args=["currency"])
        base = {"default_currency_code": "PKR", "default_currency_symbol": "Rs", "decimals": 2, "decimal_separator": ".", "thousands_separator": ",",
                "display_currency_symbol": "on"}
        self.assertRedirects(self.client.post(cur, {**base, "currency_position": "after"}), cur)
        cfg = SystemConfiguration.get()
        self.assertEqual(cfg.currency_position, "after")
        out = Template("{% load ui %}{% money 1234.5 %}").render(Context({"system_settings": cfg, "current_store": self.w.store}))
        self.assertEqual(out, "1,234.50 Rs")
        self.assertContains(self.client.get(cur), "cur-preview")
