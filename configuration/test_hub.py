"""Settings hub + the "store-less superuser" fixes (dashboard charts, counter warehouse)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from company.models import Company
from configuration import hub
from configuration.models import NumberingPrefix, POSConfiguration, SystemConfiguration
from core.pos_fixtures import PASSWORD, PosWorld, grant
from documents.models import EmailTemplate, InvoiceTemplate
from register.models import Register
from tenants.models import Store, Warehouse


AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


class HubTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)

    def url(self, slug):
        return reverse("configuration:hub_section", args=[slug])

    def test_every_section_opens(self):
        for sec in hub.SECTIONS:
            r = self.client.get(self.url(sec["slug"]))
            self.assertEqual(r.status_code, 200, sec["slug"])
            self.assertContains(r, escape(sec["label"]))
        # the index lands on the first section
        self.assertRedirects(self.client.get(reverse("configuration:hub")), self.url("business"))

    def test_old_addresses_still_work(self):
        self.assertRedirects(self.client.get(reverse("configuration:pos_settings")), self.url("pos-behaviour"))
        self.assertRedirects(self.client.get(reverse("configuration:system_settings")), self.url("business"))
        self.assertEqual(self.client.get(reverse("configuration:numbering_prefixes")).status_code, 200)
        self.assertEqual(self.client.get(reverse("configuration:currency_list")).status_code, 200)

    def test_unknown_section_is_404(self):
        self.assertEqual(self.client.get("/settings/nope/").status_code, 404)

    def test_business_saves_system_and_store(self):
        r = self.client.post(self.url("business"), {
            "site_name": "Gold Diamond", "tagline": "Since 1990", "phone": "0300 1112223", "email": "a@b.com", "website": "gd.pk",
            "tax_number": "123-4", "address": "Main road", "default_timezone": "Asia/Karachi", "rows_per_page": 50, "quantity_decimals": 2})
        self.assertRedirects(r, self.url("business"))
        cfg = SystemConfiguration.get()
        self.assertEqual((cfg.site_name, cfg.tagline, cfg.tax_number, cfg.rows_per_page, cfg.quantity_decimals), ("Gold Diamond", "Since 1990", "123-4", 50, 2))
        store = Store.objects.get(pk=self.w.store.pk)
        self.assertEqual((store.phone, store.email, store.address), ("0300 1112223", "a@b.com", "Main road"))

    def test_business_name_is_required(self):
        r = self.client.post(self.url("business"), {"site_name": "", "default_timezone": "Asia/Karachi", "rows_per_page": 25, "quantity_decimals": 0})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "This field is required")

    def test_colours_must_be_real_hex_codes(self):
        bad = self.client.post(self.url("branding"), {"primary_color": "#12345g"})
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "must be a colour")
        evil = self.client.post(self.url("branding"), {"primary_color": "red;}</style><script>alert(1)</script>"})
        self.assertEqual(evil.status_code, 200)                         # too long / not hex: refused, nothing saved
        self.assertEqual(SystemConfiguration.get().primary_color, "#4f46e5")
        ok = self.client.post(self.url("branding"), {"primary_color": "#0F766E"})
        self.assertRedirects(ok, self.url("branding"))
        self.assertEqual(SystemConfiguration.get().primary_color, "#0f766e")
        r = self.client.post(self.url("menu-fonts"), {"secondary_color": "#123456", "font_family": "Poppins"})
        self.assertRedirects(r, self.url("menu-fonts"))
        self.assertEqual(SystemConfiguration.get().font_family, "Poppins")

    def test_currency_updates_the_store_too(self):
        r = self.client.post(self.url("currency"), {"default_currency_code": "usd", "default_currency_symbol": "$", "decimals": 2,
                                                    "decimal_separator": ".", "thousands_separator": ",", "display_currency_symbol": "on"})
        self.assertRedirects(r, self.url("currency"))
        store = Store.objects.get(pk=self.w.store.pk)
        self.assertEqual((store.currency_code, store.currency_symbol), ("USD", "$"))
        same = self.client.post(self.url("currency"), {"default_currency_code": "USD", "default_currency_symbol": "$", "decimals": 2,
                                                       "decimal_separator": ",", "thousands_separator": ","})
        self.assertContains(same, "must be different")

    def test_stock_tax_rewards_scanners_save(self):
        r = self.client.post(self.url("stock"), {"allow_over_selling": "on", "enable_product_variants": "on", "edit_lock_days": 30})
        self.assertRedirects(r, self.url("stock"))
        cfg = SystemConfiguration.get()
        self.assertTrue(cfg.allow_over_selling)
        self.assertFalse(cfg.enable_racks)
        self.assertEqual(cfg.edit_lock_days, 30)
        self.assertRedirects(self.client.post(self.url("tax"), {"order_tax_inclusive": "on"}), self.url("tax"))
        self.assertTrue(SystemConfiguration.get().order_tax_inclusive)
        self.assertRedirects(self.client.post(self.url("rewards"), {"award_points_per_amount": "200", "award_points_value": 3}), self.url("rewards"))
        self.assertEqual(SystemConfiguration.get().award_points_value, 3)
        bad = self.client.post(self.url("rewards"), {"award_points_per_amount": "0", "award_points_value": 1})
        self.assertContains(bad, "greater than zero")
        self.assertRedirects(self.client.post(self.url("scanners"), {"barcode_scan_min_chars": 6, "barcode_renderer": "code39", "barcode_separator": "-"}), self.url("scanners"))
        self.assertEqual(SystemConfiguration.get().barcode_renderer, "code39")

    def test_defaults_save_system_and_pos(self):
        r = self.client.post(self.url("defaults"), {"default_warehouse": self.w.warehouse.pk, "default_language": "en", "date_format": "Y-m-d",
                                                    "pos_default_warehouse": self.w.warehouse.pk, "pos_default_biller": self.w.cashier.pk})
        self.assertRedirects(r, self.url("defaults"))
        self.assertEqual(SystemConfiguration.get().default_warehouse_id, self.w.warehouse.pk)
        pc = POSConfiguration.objects.get(store=self.w.store)
        self.assertEqual((pc.default_warehouse_id, pc.default_biller_id), (self.w.warehouse.pk, self.w.cashier.pk))

    def test_pos_behaviour_customer_rule_can_be_switched_off_and_on(self):
        base = {"products_per_page": 40, "item_order": "name", "after_sale_page": "receipt", "rounding": "none", "product_button_color": "#2563eb"}
        self.client.post(self.url("pos-behaviour"), dict(base))                       # box left empty -> off
        self.assertFalse(POSConfiguration.objects.get(store=self.w.store).require_customer)
        self.client.post(self.url("pos-behaviour"), dict(base, require_customer="on"))
        self.assertTrue(POSConfiguration.objects.get(store=self.w.store).require_customer)
        self.assertContains(self.client.get(self.url("pos-behaviour")), "Ask for a customer before adding items")

    def test_pos_behaviour_and_receipt(self):
        r = self.client.post(self.url("pos-behaviour"), {"products_per_page": 40, "item_order": "name", "after_sale_page": "new_sale", "rounding": "none",
                                                         "product_button_color": "#2563eb", "pos_access_pin": "1234", "show_clock": "on"})
        self.assertRedirects(r, self.url("pos-behaviour"))
        self.assertEqual(POSConfiguration.objects.get(store=self.w.store).after_sale_page, "new_sale")
        self.assertContains(self.client.post(self.url("pos-behaviour"), {"products_per_page": 40, "item_order": "name", "after_sale_page": "receipt", "rounding": "none",
                                                                          "product_button_color": "#2563eb", "pos_access_pin": "12ab"}), "digits only")
        r = self.client.post(self.url("receipt"), {"printing_mode": "browser", "footer_text": "Thanks!", "terms_and_conditions": "No refunds", "show_logo": "on",
                                                   "receipt_custom_field1_title": "NTN", "receipt_custom_field1_value": "99"})
        self.assertRedirects(r, self.url("receipt"))
        tpl = InvoiceTemplate.objects.get(store=self.w.store)
        self.assertEqual((tpl.footer_text, tpl.is_default), ("Thanks!", True))
        # saving again edits the same template, never piles up copies
        self.client.post(self.url("receipt"), {"printing_mode": "browser", "footer_text": "Bye", "show_logo": "on"})
        self.assertEqual(InvoiceTemplate.objects.filter(store=self.w.store).count(), 1)

    def test_numbering(self):
        self.client.get(self.url("numbering"))
        row = NumberingPrefix.objects.get(document_type="sale")
        data = {f"prefix_{r.pk}": r.prefix for r in NumberingPrefix.objects.all()}
        data.update({f"padding_{r.pk}": r.padding for r in NumberingPrefix.objects.all()})
        data[f"prefix_{row.pk}"] = "inv"
        data[f"padding_{row.pk}"] = "5"
        self.assertRedirects(self.client.post(self.url("numbering"), data), self.url("numbering"))
        row.refresh_from_db()
        self.assertEqual((row.prefix, row.padding), ("INV", 5))
        self.assertEqual(NumberingPrefix.next_reference("sale"), "INV-00001")
        data[f"prefix_{row.pk}"] = "bad prefix!"
        self.assertContains(self.client.post(self.url("numbering"), data), "Use letters, digits")

    def test_email_delivery_and_test_mail(self):
        r = self.client.post(self.url("email"), {"email_from_name": "Shop", "email_from_address": "billing@shop.pk", "email_reply_to": "help@shop.pk"})
        self.assertRedirects(r, self.url("email"))
        r = self.client.post(self.url("email"), {"action": "test_email", "test_to": "me@example.com"})
        self.assertRedirects(r, self.url("email"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, "Shop <billing@shop.pk>")
        self.assertEqual(mail.outbox[0].reply_to, ["help@shop.pk"])

    def test_whatsapp_placeholders_are_checked(self):
        ok = self.client.post(self.url("whatsapp"), {"whatsapp_country_code": "+92", "whatsapp_message": "Hi {customer_name}, {reference}"})
        self.assertRedirects(ok, self.url("whatsapp"))
        self.assertEqual(SystemConfiguration.get().whatsapp_country_code, "92")
        self.assertContains(self.client.post(self.url("whatsapp"), {"whatsapp_country_code": "92", "whatsapp_message": "Hi {nope}"}), "Unknown placeholder")
        self.assertContains(self.client.post(self.url("whatsapp"), {"whatsapp_country_code": "92", "whatsapp_message": "Hi {oops"}), "brackets")

    def test_email_templates_crud(self):
        base = self.url("email-templates")
        self.assertEqual(self.client.get(base + "?new=1").status_code, 200)
        r = self.client.post(base, {"name": "Std", "subject": "Invoice {{ reference }}", "body": "Hi {{ customer_name }}", "is_default": "on"})
        self.assertRedirects(r, base)
        tpl = EmailTemplate.objects.get(store=self.w.store)
        self.assertTrue(tpl.is_default)
        self.client.post(base, {"name": "Second", "subject": "S", "body": "B", "is_default": "on"})
        tpl.refresh_from_db()
        self.assertFalse(tpl.is_default)                               # only one template is "in use"
        self.assertEqual(self.client.get(base + f"?edit={tpl.pk}").status_code, 200)
        self.assertContains(self.client.post(base, {"name": "X", "subject": "{% bogus %}", "body": "B"}), "mistake in the placeholders")
        self.client.post(base, {"action": "delete", "pk": tpl.pk})
        self.assertFalse(EmailTemplate.objects.filter(pk=tpl.pk).exists())

    def test_user_without_permission_is_refused(self):
        grant(self.w.cashier, "configuration.change_posconfiguration")
        self.client.logout()
        self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(self.url("pos-behaviour")).status_code, 200)
        self.assertEqual(self.client.get(self.url("business")).status_code, 403)
        # the list only shows sections the user may open
        r = self.client.get(self.url("pos-behaviour"))
        self.assertNotContains(r, self.url("business"))

    def test_payment_and_device_sections_list_this_branch(self):
        from devices.models import Device
        Device.objects.create(store=self.w.store, type="printer", name="Front printer", connection_info="192.168.1.5:9100")
        self.assertContains(self.client.get(self.url("printers")), "Front printer")
        self.assertContains(self.client.get(self.url("payments")), "Cash")


class StorelessSuperuserTests(TestCase):
    """A superuser account created with `createsuperuser` has no branch. Everything must still work for it."""

    def setUp(self):
        company = Company.objects.create(name="Acme")
        self.store = Store.objects.create(name="Main", company=company)
        self.admin = get_user_model().objects.create_superuser("root", "r@example.com", PASSWORD)
        self.assertIsNone(self.admin.store)
        self.client.login(username="root", password=PASSWORD)

    def test_counter_can_be_created_without_picking_a_warehouse(self):
        r = self.client.post(reverse("register:counter_add"), {"name": "Counter 9", "code": "C9", "is_active": "on"}, **AJAX)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"], r.content[:400])
        counter = Register.objects.get(code="C9")
        self.assertEqual(counter.store, self.store)
        self.assertIsNotNone(counter.warehouse)                        # a default warehouse was created on the spot
        self.assertTrue(Warehouse.objects.filter(store=self.store, is_default=True).exists())

    def test_counter_form_lists_the_branch_warehouses(self):
        Warehouse.objects.create(store=self.store, name="Back store", warehouse_type=Warehouse.GENERAL)
        r = self.client.get(reverse("register:counter_add"), **AJAX)
        self.assertContains(r, "Back store")

    def test_counter_with_chosen_warehouse(self):
        wh = Warehouse.objects.create(store=self.store, name="Shelf", warehouse_type=Warehouse.OUTLET)
        self.client.post(reverse("register:counter_add"), {"name": "Counter 2", "code": "C2", "warehouse": wh.pk, "is_active": "on"}, **AJAX)
        self.assertEqual(Register.objects.get(code="C2").warehouse, wh)

    def test_dashboard_has_data_for_the_charts(self):
        r = self.client.get(reverse("dashboard:home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "overviewChart")
        self.assertContains(r, "dailyChart")
        self.assertContains(r, "statusChart")
        self.assertContains(r, "vendor/chart.umd.js")
        self.assertEqual(r.context["request"].store, self.store)

    def test_settings_hub_works_for_a_storeless_superuser(self):
        self.assertEqual(self.client.get(reverse("configuration:hub_section", args=["pos-behaviour"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("configuration:hub_section", args=["business"])).status_code, 200)


class ReceiptAndDocumentsWiringTests(TestCase):
    """Settings really reach the receipt, the invoice email and the WhatsApp text."""

    def setUp(self):
        from decimal import Decimal
        from sales.models import Sale
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        cfg = SystemConfiguration.get()
        cfg.tagline, cfg.website, cfg.tax_number = "Fine jewellery", "gd.pk", "NTN-77"
        cfg.save()
        InvoiceTemplate.objects.create(store=self.w.store, name="Default", is_default=True, footer_text="Visit again soon", terms_and_conditions="7 day exchange")
        pc = POSConfiguration.objects.create(store=self.w.store, receipt_custom_field1_title="GST No", receipt_custom_field1_value="55-66")
        self.sale = Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, biller=self.w.manager, reference="SALE-000001",
                                        subtotal=Decimal("100"), total_amount=Decimal("110"))

    def test_receipt_shows_business_details_and_wording(self):
        r = self.client.get(reverse("sales:invoice_print", args=[self.sale.pk]))
        self.assertContains(r, "NTN-77")
        self.assertContains(r, "gd.pk")
        self.assertContains(r, "Fine jewellery")
        self.assertContains(r, "Visit again soon")
        self.assertContains(r, "7 day exchange")
        self.assertContains(r, "GST No")

    def test_whatsapp_text_and_number(self):
        from configuration.mailing import whatsapp_number
        from documents.services import _whatsapp_text
        cfg = SystemConfiguration.get()
        cfg.whatsapp_message = "Dear {customer_name}, {reference} = {total} from {store_name}"
        text = _whatsapp_text(cfg, self.sale)
        self.assertIn("SALE-000001", text)
        self.assertIn("Main", text)
        self.assertEqual(whatsapp_number("0300-1234567", "92"), "923001234567")
        self.assertEqual(whatsapp_number("+92 300 1234567", "92"), "923001234567")
        cfg.whatsapp_message = "{broken"
        self.assertIn("Thank you", _whatsapp_text(cfg, self.sale))          # never raises on a bad saved message

    def test_email_uses_default_template_and_sender(self):
        from documents.services import _invoice_email_content
        self.assertEqual(_invoice_email_content(self.sale), ("Invoice SALE-000001", "Your invoice SALE-000001 is attached."))
        EmailTemplate.objects.create(store=self.w.store, name="T", subject="Bill {{ reference }}", body="Hello {{ customer_name }} — {{ total }}", is_default=True)
        subject, body = _invoice_email_content(self.sale)
        self.assertEqual(subject, "Bill SALE-000001")
        self.assertIn("Hello Customer", body)


class SignInAndPasswordResetTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.user = get_user_model().objects.create_user("owner", "owner@example.com", PASSWORD, store=self.w.store)

    def test_login_page_has_forgot_link_and_signs_in(self):
        page = self.client.get(reverse("accounts:login"))
        self.assertContains(page, reverse("accounts:password_reset"))
        self.assertContains(page, "Forgot password?")
        r = self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD, "remember": "1"})
        self.assertEqual(r.status_code, 302)

    def test_wrong_password_shows_message(self):
        r = self.client.post(reverse("accounts:login"), {"username": "owner", "password": "nope"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "alert")

    def test_not_remembered_session_ends_with_browser(self):
        self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD})
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.client.logout()
        self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD, "remember": "1"})
        self.assertFalse(self.client.session.get_expire_at_browser_close())

    def test_full_reset_flow(self):
        cfg = SystemConfiguration.get()
        cfg.email_from_name, cfg.email_from_address = "Gold Diamond", "no-reply@gd.pk"
        cfg.save()
        r = self.client.post(reverse("accounts:password_reset"), {"email": "owner@example.com"})
        self.assertRedirects(r, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ["owner@example.com"])
        self.assertEqual(msg.from_email, "Gold Diamond <no-reply@gd.pk>")
        self.assertIn("owner", msg.body)
        import re
        link = re.search(r"/accounts/reset/[^/\s]+/[^/\s]+/", msg.body).group(0)
        page = self.client.get(link, follow=True)
        self.assertContains(page, "Choose a new password")
        post_url = page.redirect_chain[-1][0] if page.redirect_chain else link
        done = self.client.post(post_url, {"new_password1": "Br4nd-new-pass!", "new_password2": "Br4nd-new-pass!"})
        self.assertRedirects(done, reverse("accounts:password_reset_complete"))
        self.assertTrue(self.client.login(username="owner", password="Br4nd-new-pass!"))
        # the link only works once
        from django.test import Client
        again = Client().get(link, follow=True)
        self.assertContains(again, "Link not valid")

    def test_unknown_email_gets_the_same_answer_and_no_mail(self):
        r = self.client.post(reverse("accounts:password_reset"), {"email": "ghost@example.com"})
        self.assertRedirects(r, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_requests_are_rate_limited(self):
        from django.core.cache import cache
        cache.clear()
        for _ in range(5):
            self.client.post(reverse("accounts:password_reset"), {"email": "owner@example.com"})
        r = self.client.post(reverse("accounts:password_reset"), {"email": "owner@example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Too many requests")
        cache.clear()

    def test_mail_failure_does_not_crash(self):
        from unittest import mock
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            r = self.client.post(reverse("accounts:password_reset"), {"email": "owner@example.com"})
        # Django 5.0/5.1 raise (we show a friendly error); Django 5.2+ logs the failure and answers "check your email" on purpose
        # (so the form can't be used to find out which addresses exist). Either way: no crash.
        self.assertIn(r.status_code, (200, 302))
        if r.status_code == 200:
            self.assertContains(r, "could not send the email")


class DashboardNumbersTests(TestCase):
    """The dashboard's new aggregates, checked against hand-made sales."""

    def setUp(self):
        from payments.models import Payment
        from sales.models import Sale, SaleItem
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)

        def sale(ref, total, qty, variant, method, amount):
            s = Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, biller=self.w.manager, reference=ref, subtotal=Decimal(total), total_amount=Decimal(total))
            SaleItem.objects.create(sale=s, variant=variant, quantity=Decimal(qty), unit_price=Decimal(total) / Decimal(qty))
            Payment.objects.create(sale=s, reference="P-" + ref, amount=Decimal(amount), method=method, received_by=self.w.manager)
            return s
        sale("S1", "300", "3", self.w.tea, "cash", "300")
        sale("S2", "100", "2", self.w.milk, "card", "40")           # part-paid: 60 due

    def test_today_hero_and_breakdowns(self):
        ctx = self.client.get(reverse("dashboard:home")).context
        self.assertEqual(ctx["today_total"], Decimal("400"))
        self.assertEqual(ctx["today_orders"], 2)
        self.assertIsNone(ctx["today_change"])                       # nothing yesterday to compare with
        self.assertEqual({p["total"] for p in ctx["pay_methods"]}, {Decimal("300"), Decimal("40")})
        self.assertEqual(ctx["top_products"][0]["variant__product__name"], "Tea")
        self.assertEqual(ctx["top_products"][0]["pct"], 100)
        self.assertEqual(ctx["d30_total"], 400)
        self.assertEqual(len(ctx["chart_labels_json"].split(",")), 12)
        self.assertEqual((ctx["status_paid"], ctx["status_partial"]), (1, 1))

    def test_low_stock_uses_the_products_alert_level(self):
        tea = self.w.tea.product
        tea.alert_quantity = Decimal("20")                            # stock is 10 -> low
        tea.save()
        ctx = self.client.get(reverse("dashboard:home")).context
        self.assertEqual(ctx["low_count"], 1)
        self.assertEqual(ctx["low_stock"][0].variant, self.w.tea)
        self.assertContains(self.client.get(reverse("dashboard:home")), "Tea")

    def test_quick_actions_respect_permissions(self):
        labels = [a["label"] for a in self.client.get(reverse("dashboard:home")).context["quick_actions"]]
        self.assertIn("New sale", labels)
        self.client.logout()
        self.client.login(username="cashier", password=PASSWORD)
        labels = [a["label"] for a in self.client.get(reverse("dashboard:home")).context["quick_actions"]]
        self.assertEqual(labels, ["New sale", "Reports"])            # no add-product / add-customer rights
