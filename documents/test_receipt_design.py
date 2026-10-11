"""Receipt / invoice designer (Settings > Receipt) + automatic printing after a sale."""
import json

from django.test import TestCase
from django.urls import reverse

from configuration.models import POSConfiguration
from core.pos_fixtures import PASSWORD, PosWorld
from documents.invoice_pdf import render_invoice_pdf_bytes
from documents.models import InvoiceTemplate
from documents.receipt_design import resolve
from register import services
from sales.models import Sale


class DesignResolverTests(TestCase):
    def test_defaults_and_overrides(self):
        d = resolve()
        self.assertEqual((d.paper, d.style, d.title, d.is_thermal), ("a4", "classic", "INVOICE", False))
        d = resolve(None, None, {"paper": "thermal58", "accent_color": "#ff0000", "title_text": "  "})
        self.assertTrue(d.is_thermal and d.is_compact)
        self.assertEqual((d.accent, d.title), ("#ff0000", "INVOICE"))
        self.assertEqual(resolve(None, None, {"accent_color": "red"}).accent, "#4f46e5")      # not a hex colour -> brand fallback


class ReceiptDesignerTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        session = services.open_session(self.w.counter1, self.w.manager, 100)
        s = self.client.session
        s["active_register_session_id"] = session.pk
        s.save()
        body = {"items": [{"variant_id": self.w.tea.pk, "quantity": "2"}], "payments": [{"method": "cash", "amount": "300"}]}
        self.sale_json = self.client.post(reverse("sales:pos_finalize"), data=json.dumps(body), content_type="application/json").json()
        self.assertTrue(self.sale_json["ok"], self.sale_json)
        self.sale = Sale.objects.get(pk=self.sale_json["sale_id"])

    def url(self):
        return reverse("configuration:hub_section", args=["receipt"])

    def test_settings_page_has_designer_and_preview(self):
        r = self.client.get(self.url())
        self.assertContains(r, "Paper")
        self.assertContains(r, "Print automatically when a sale is completed")
        self.assertContains(r, reverse("configuration:receipt_preview"))

    def test_design_is_saved_and_used_by_print_page(self):
        r = self.client.post(self.url(), {"paper": "thermal80", "style": "modern", "font_style": "mono", "font_size": "large", "header_align": "center",
                                          "title_text": "TAX RECEIPT", "accent_color": "#aa0000", "show_biller": "on", "show_item_sku": "on",
                                          "footer_text": "See you soon", "printing_mode": "browser", "auto_print_receipt": "on"})
        self.assertRedirects(r, self.url())
        tpl = InvoiceTemplate.objects.get(store=self.w.store)
        self.assertEqual((tpl.paper, tpl.style, tpl.title_text, tpl.accent_color, tpl.show_item_sku, tpl.show_notes),
                         ("thermal80", "modern", "TAX RECEIPT", "#aa0000", True, False))
        html = self.client.get(reverse("sales:invoice_print", args=[self.sale.pk])).content.decode()
        for text in ("TAX RECEIPT", "80mm auto", "#aa0000", "See you soon"):
            self.assertIn(text, html)

    def test_brand_colour_checkbox_clears_custom_colour(self):
        self.client.post(self.url(), {"accent_color": "#123456", "printing_mode": "browser"})
        self.assertEqual(InvoiceTemplate.objects.get(store=self.w.store).accent_color, "#123456")
        self.client.post(self.url(), {"accent_color": "#123456", "use_brand_color": "on", "printing_mode": "browser"})
        self.assertEqual(InvoiceTemplate.objects.get(store=self.w.store).accent_color, "")
        self.assertContains(self.client.post(self.url(), {"accent_color": "nope", "printing_mode": "browser"}), "Pick a colour")

    def test_auto_print_default_on_and_can_be_switched_off(self):
        self.assertTrue(self.sale_json["auto_print"])
        self.client.post(self.url(), {"printing_mode": "browser"})                 # box left empty -> off
        self.assertFalse(POSConfiguration.objects.get(store=self.w.store).auto_print_receipt)
        self.client.post(self.url(), {"printing_mode": "browser", "auto_print_receipt": "on"})
        self.assertTrue(POSConfiguration.objects.get(store=self.w.store).auto_print_receipt)

    def test_finalize_reports_auto_print_off(self):
        POSConfiguration.objects.update_or_create(store=self.w.store, defaults={"auto_print_receipt": False})
        body = {"items": [{"variant_id": self.w.milk.pk, "quantity": "1"}], "payments": [{"method": "cash", "amount": "50"}]}
        data = self.client.post(reverse("sales:pos_finalize"), data=json.dumps(body), content_type="application/json").json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["auto_print"])

    def test_preview_uses_unsaved_values_and_sample_sale(self):
        r = self.client.get(reverse("configuration:receipt_preview"), {"paper": "thermal58", "title_text": "DRAFT", "show_item_sku": "1", "style": "bogus"})
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for text in ("DRAFT", "58mm auto", "RICE-5K", "INV-000123"):
            self.assertIn(text, html)
        self.assertNotIn("window.print", html)                                      # the preview never opens the print dialog

    def test_preview_needs_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("configuration:receipt_preview")).status_code, 302)

    def test_embed_print_has_no_buttons_but_still_prints(self):
        html = self.client.get(reverse("sales:invoice_print", args=[self.sale.pk]), {"embed": "1"}).content.decode()
        self.assertNotIn('class="toolbar', html)
        self.assertIn("window.print", html)
        full = self.client.get(reverse("sales:invoice_print", args=[self.sale.pk])).content.decode()
        self.assertIn('class="toolbar', full)

    def test_pdf_follows_design(self):
        self.assertTrue(render_invoice_pdf_bytes(self.sale).startswith(b"%PDF"))
        for paper in ("thermal80", "thermal58", "a4"):
            InvoiceTemplate.objects.update_or_create(store=self.w.store, name="Default", defaults={
                "is_default": True, "paper": paper, "style": "modern", "header_align": "center", "title_text": "RECEIPT", "show_item_sku": True,
                "terms_and_conditions": "No refunds", "footer_text": "Bye"})
            self.assertTrue(render_invoice_pdf_bytes(self.sale).startswith(b"%PDF"), paper)
