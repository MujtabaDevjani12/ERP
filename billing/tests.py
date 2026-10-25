"""
ERP side of the subscription system: sign in / sign up, "locked until the first payment", the company's own billing page,
plan enforcement and limits, Stripe + Google, tenant scoping. The owner console has its own tests (ownerconsole/tests.py).
"""
import hashlib
import hmac
import json
import time
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from billing import access, bridge, services, stripe_gateway
from billing.google_auth import GoogleAuthError, verify_id_token
from billing.tenancy import scope_queryset
from billing.testkit import BillingBase, User, make_owner, make_plans, pay, signup
from company.models import Company
from core.errors import ERPError
from core.pos_fixtures import PASSWORD, grant
from notifications.models import Notification
from ownerconsole import services as owner
from ownerconsole.models import BillingEvent, EmailLog, Invoice, Payment, PlatformSettings, Plan, Subscription
from tenants.models import Store, Warehouse


# ------------------------------------------------------------------------------------------------------------------ sign-up
class SignupPendingTests(BillingBase):
    def test_signup_creates_company_admin_and_waits_for_the_first_payment(self):
        company, user, sub = signup("Zeta Traders", "zeta@example.com", self.starter)
        self.assertEqual(sub.status, Subscription.PENDING)
        self.assertEqual(sub.state(), "pending")
        self.assertFalse(sub.has_access())
        self.assertIsNone(sub.trial_ends_at)
        self.assertEqual(sub.company_id, company.pk)
        self.assertEqual(sub.company_name, "Zeta Traders")
        self.assertTrue(Store.objects.filter(company=company).exists())
        self.assertTrue(Warehouse.objects.filter(store__company=company).exists())
        self.assertTrue(user.groups.filter(name="Company Admin").exists())
        self.assertTrue(user.has_perm("billing.view_subscription"))
        self.assertTrue(user.has_perm("billing.add_payment"))
        self.assertFalse(user.is_superuser)

    def test_first_invoice_is_created_at_signup(self):
        _, _, sub = signup("Inv Co", "inv@example.com", self.starter, "yearly")
        invoices = list(sub.invoices.all())
        self.assertEqual(len(invoices), 1)
        self.assertEqual((invoices[0].status, invoices[0].kind, invoices[0].total), (Invoice.OPEN, Invoice.FIRST, Decimal("25000.00")))
        self.assertTrue(BillingEvent.objects.filter(company_id=sub.company_id, kind="signup").exists())

    def test_welcome_mail_goes_to_the_company_and_the_owners_are_alerted(self):
        make_owner()
        signup("Mail Co", "mail@example.com", self.starter)
        recipients = {m.to[0] for m in mail.outbox}
        self.assertIn("mail@example.com", recipients)
        self.assertIn("boss@owner.test", recipients)
        self.assertTrue(EmailLog.objects.filter(kind="welcome", status=EmailLog.SENT).exists())

    def test_company_mails_can_be_switched_off(self):
        s = PlatformSettings.get()
        s.send_company_emails = False
        s.save()
        signup("Quiet Co", "quiet@example.com", self.starter)
        self.assertFalse(EmailLog.objects.filter(kind="welcome").exists())

    def test_duplicate_email_is_refused(self):
        signup("One", "same@example.com", self.starter)
        with self.assertRaises(ERPError):
            signup("Two", "same@example.com", self.starter)

    def test_signup_is_refused_when_no_plan_is_published(self):
        Plan.objects.all().delete()
        with self.assertRaises(ERPError):
            services.create_company_with_owner(company_name="No Plan", owner_name="X", email="np@example.com", password=PASSWORD)
        self.assertFalse(Company.objects.filter(name="No Plan").exists())

    def test_if_the_owner_database_fails_nothing_is_left_behind(self):
        with mock.patch("billing.services.owner_services.register_company", side_effect=RuntimeError("db down")):
            with self.assertRaises(ERPError):
                signup("Broken Co", "broken@example.com", self.starter)
        self.assertFalse(Company.objects.filter(name="Broken Co").exists())
        self.assertFalse(User.objects.filter(email="broken@example.com").exists())

    def test_platform_default_demo_days_open_the_erp_straight_away(self):
        s = PlatformSettings.get()
        s.trial_days = 7
        s.save()
        _, _, sub = signup("Seven", "seven@example.com", self.starter)
        self.assertEqual(sub.state(), "trial")
        self.assertTrue(sub.has_access())
        self.assertIn(sub.days_left(), (6, 7))

    def test_a_plan_can_have_its_own_demo_days(self):
        self.starter.trial_days = 3
        self.starter.save()
        _, _, sub = signup("Plan Demo", "pd@example.com", self.starter)
        self.assertEqual(sub.state(), "trial")
        self.assertIn(sub.days_left(), (2, 3))


class SignupLegalLinksTests(BillingBase):
    def test_terms_and_privacy_links_show_under_the_signup_form(self):
        s = PlatformSettings.get()
        s.terms_url, s.privacy_url = "https://example.com/terms", "https://example.com/privacy"
        s.save()
        r = self.client.get(reverse("billing:signup"))
        self.assertContains(r, 'href="https://example.com/terms"')
        self.assertContains(r, 'href="https://example.com/privacy"')
        s.terms_url = s.privacy_url = ""
        s.save()
        self.assertNotContains(self.client.get(reverse("billing:signup")), "Terms of service")


class LockedUntilPaidTests(BillingBase):
    def setUp(self):
        self.company, self.user, self.sub = signup("Lock Co", "lock@example.com", self.starter)
        self.client.force_login(self.user)

    def test_every_erp_page_redirects_to_billing_until_the_first_payment(self):
        for url in (reverse("dashboard:home"), reverse("catalog:product_list"), reverse("accounts:staff_user_list")):
            r = self.client.get(url)
            self.assertRedirects(r, reverse("billing:home"), fetch_redirect_response=False, msg_prefix=url)

    def test_ajax_calls_get_a_402_not_a_redirect(self):
        r = self.client.get(reverse("catalog:product_list"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 402)

    def test_billing_page_explains_what_to_do_and_shows_the_first_invoice(self):
        r = self.client.get(reverse("billing:home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Starter")
        self.assertContains(r, self.sub.invoices.first().number)
        self.assertEqual(r.context["state"], "pending")
        self.assertTrue(r.context["locked"])

    def test_sidebar_offers_only_billing(self):
        r = self.client.get(reverse("billing:home"))
        self.assertContains(r, 'href="/billing/"')
        self.assertNotContains(r, reverse("catalog:product_list"))
        self.assertNotContains(r, reverse("accounts:staff_user_list"))

    def test_login_and_logout_pages_stay_reachable(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("accounts:login")).status_code, 200)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("billing:home")).status_code, 200)

    def test_paying_the_first_invoice_unlocks_the_erp(self):
        pay(self.sub)
        self.assertEqual(self.sub.state(), "active")
        self.assertEqual(self.client.get(reverse("catalog:product_list")).status_code, 200)
        r = self.client.get(reverse("billing:home"))
        self.assertFalse(r.context["locked"])

    def test_manual_receipt_alone_does_not_unlock_only_approval_does(self):
        inv = owner.open_invoice(self.sub)
        r = self.client.post(reverse("billing:pay_manual", args=[inv.pk]), {"method": "jazzcash", "reference": "JC-1"})
        self.assertRedirects(r, reverse("billing:home"))
        self.assertEqual(self.client.get(reverse("catalog:product_list")).status_code, 302)
        owner.approve_payment(inv.payments.get(), make_owner())
        self.assertEqual(self.client.get(reverse("catalog:product_list")).status_code, 200)

    def test_owner_gives_demo_days_and_the_erp_opens_without_payment(self):
        owner.extend_trial(self.sub, 5, make_owner())
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "trial")
        self.assertEqual(self.client.get(reverse("catalog:product_list")).status_code, 200)
        self.assertContains(self.client.get(reverse("dashboard:home")), "free demo ends in")

    def test_demo_that_ends_unpaid_locks_at_once_without_grace(self):
        owner.extend_trial(self.sub, 5, make_owner())
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(self.sub.trial_ends_at + timedelta(hours=1)), "expired")
        self.sub.trial_ends_at = timezone.now() - timedelta(hours=1)
        self.sub.save()
        r = self.client.get(reverse("catalog:product_list"))
        self.assertRedirects(r, reverse("billing:home"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("billing:home")), "trial has ended", status_code=200)

    def test_suspended_company_is_locked_even_after_paying(self):
        pay(self.sub)
        owner.suspend(self.sub, "Fraud", make_owner())
        r = self.client.get(reverse("catalog:product_list"))
        self.assertRedirects(r, reverse("billing:home"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("billing:home")), "Fraud")

    def test_staff_without_billing_rights_see_a_locked_page(self):
        staff = User.objects.create_user("clerk", "clerk@example.com", PASSWORD, store=self.user.store)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("billing:home")).status_code, 402)
        self.assertEqual(self.client.get(reverse("catalog:product_list")).status_code, 302)


class ExpiryAndGraceTests(BillingBase):
    def setUp(self):
        self.company, self.user, self.sub = signup("Grace Co", "grace@example.com", self.starter)
        pay(self.sub)
        self.client.force_login(self.user)

    def test_grace_period_after_a_paid_period_still_works_but_warns(self):
        self.sub.current_period_end = timezone.now() - timedelta(days=2)
        self.sub.save()
        r = self.client.get(reverse("catalog:product_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "payment is overdue")

    def test_after_grace_everything_is_locked(self):
        self.sub.current_period_end = timezone.now() - timedelta(days=30)
        self.sub.save()
        self.assertRedirects(self.client.get(reverse("catalog:product_list")), reverse("billing:home"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("billing:home")).status_code, 200)

    def test_paying_again_unlocks(self):
        self.sub.current_period_end = timezone.now() - timedelta(days=30)
        self.sub.save()
        pay(self.sub)
        self.assertEqual(self.client.get(reverse("catalog:product_list")).status_code, 200)

    def test_companies_without_a_subscription_and_super_admins_are_never_restricted(self):
        self.client.force_login(self.erp_admin)
        self.assertEqual(self.client.get(reverse("suppliers:supplier_list")).status_code, 200)
        legacy = Company.objects.create(name="Legacy")
        store = Store.objects.create(name="L", company=legacy)
        legacy_user = User.objects.create_user("legacy", "legacy@example.com", PASSWORD, store=store, is_staff=True)
        grant(legacy_user, "suppliers.view_supplier")
        self.client.force_login(legacy_user)
        self.assertEqual(self.client.get(reverse("suppliers:supplier_list")).status_code, 200)

    def test_if_the_owner_database_is_unreachable_the_erp_keeps_working(self):
        from django.db import DatabaseError
        with mock.patch("ownerconsole.models.Subscription.objects") as objects:
            objects.select_related.side_effect = DatabaseError("no owner db")
            self.assertIsNone(access.subscription_of(self.company))


class PlanEnforcementTests(BillingBase):
    def setUp(self):
        self.company, self.user, self.sub = signup("Plan Co", "plan@example.com", self.starter)
        pay(self.sub)
        self.client.force_login(self.user)

    def test_active_company_can_use_modules_in_its_plan(self):
        self.assertEqual(self.client.get(reverse("catalog:product_list")).status_code, 200)

    def test_module_outside_the_plan_is_blocked_with_a_friendly_page(self):
        r = self.client.get(reverse("suppliers:supplier_list"))
        self.assertEqual(r.status_code, 403)
        self.assertContains(r, "not part of your plan", status_code=403)
        self.assertEqual(self.client.get(reverse("erp_ui:module", args=["hr"])).status_code, 403)

    def test_sidebar_hides_modules_outside_the_plan(self):
        r = self.client.get(reverse("dashboard:home"))
        self.assertNotContains(r, reverse("suppliers:supplier_list"))
        self.assertContains(r, reverse("catalog:product_list"))

    def test_upgrading_unlocks_the_module(self):
        owner.mark_invoice_paid(owner.request_plan_change(self.sub, self.pro, "monthly"))
        self.assertEqual(self.client.get(reverse("suppliers:supplier_list")).status_code, 200)


class LimitTests(BillingBase):
    def setUp(self):
        self.company, self.user, self.sub = signup("Limit Co", "limit@example.com", self.starter)
        pay(self.sub)
        self.client.force_login(self.user)

    def test_usage_and_limit_rows(self):
        rows = {r["key"]: r for r in access.limit_rows(self.company)}
        self.assertEqual(rows["users"]["used"], 1)
        self.assertEqual(rows["users"]["limit"], 2)
        self.assertFalse(rows["users"]["reached"])
        self.assertFalse(rows["products"]["unlimited"])

    def test_company_override_beats_the_plan(self):
        self.sub.max_users_override = 0
        self.sub.save()
        self.assertEqual(self.sub.limit("users"), 0)
        self.assertEqual(access.limit_reached(self.company, "users"), (False, ""))

    def test_user_limit_blocks_new_staff(self):
        User.objects.create_user("second", "second@example.com", PASSWORD, store=self.user.store)
        self.assertTrue(access.limit_reached(self.company, "users")[0])
        r = self.client.get(reverse("accounts:staff_user_add"))
        self.assertRedirects(r, reverse("accounts:staff_user_list"), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("accounts:staff_user_list")), "allows 2 users")

    def test_product_limit_blocks_new_products(self):
        from catalog.models import Product, Unit
        unit = Unit.objects.create(store=self.user.store, name="Piece", short_name="pc")
        for i in range(3):
            Product.objects.create(store=self.user.store, unit=unit, name=f"P{i}", sku=f"P{i}")
        r = self.client.get(reverse("catalog:product_add"))
        self.assertRedirects(r, reverse("catalog:product_list"), fetch_redirect_response=False)

    def test_monthly_sales_limit_stops_checkout(self):
        with mock.patch("billing.access.limit_reached", return_value=(True, "Your Starter plan allows 2 sales.")):
            grant(self.user, "sales.add_sale")
            r = self.client.post(reverse("sales:pos_finalize"), json.dumps({"items": [{"variant_id": 1, "quantity": 1}]}), content_type="application/json")
        self.assertEqual(r.status_code, 402)
        self.assertEqual(r.json()["code"], "plan_limit")

    def test_branch_limit_hides_the_add_button_in_the_erp_screens(self):
        from erp_ui.registry import model_registry
        ma = next(ma for model, ma in model_registry() if model._meta.label_lower == "tenants.store")
        req = RequestFactory().get("/")
        req.user, req.company, req.store = self.user, self.company, self.user.store
        self.assertFalse(ma.has_add_permission(req))                                           # starter allows 1 branch and it has 1


# ------------------------------------------------------------------------------------------------------------------ the pages
class AuthPageTests(BillingBase):
    def test_login_page_has_sign_in_and_sign_up_tabs_not_free_trial_text(self):
        r = self.client.get(reverse("accounts:login"))
        self.assertContains(r, 'class="bp-authtabs"')
        self.assertContains(r, reverse("billing:signup"))
        self.assertContains(r, "Sign up")
        self.assertNotContains(r, "free trial")
        self.assertNotContains(r, "Create your company")

    def test_tabs_are_hidden_when_sign_up_is_closed(self):
        s = PlatformSettings.get()
        s.allow_public_signup = False
        s.save()
        r = self.client.get(reverse("accounts:login"))
        self.assertNotContains(r, 'class="bp-authtabs"')

    def test_login_page_shows_google_button_when_configured(self):
        s = PlatformSettings.get()
        s.google_client_id = "abc.apps.googleusercontent.com"
        s.save()
        r = self.client.get(reverse("accounts:login"))
        self.assertContains(r, "abc.apps.googleusercontent.com")
        self.assertContains(r, "signin_with")

    def test_signup_page_has_the_tabs_and_google_signup(self):
        s = PlatformSettings.get()
        s.google_client_id = "abc.apps.googleusercontent.com"
        s.save()
        r = self.client.get(reverse("billing:signup"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'class="bp-authtabs"')
        self.assertContains(r, "abc.apps.googleusercontent.com")

    def test_plans_page_lists_public_plans_for_visitors(self):
        Plan.objects.create(name="Secret", slug="secret", is_public=False)
        r = self.client.get(reverse("billing:plans"))
        self.assertContains(r, "Starter")
        self.assertContains(r, "Pro")
        self.assertNotContains(r, "Secret")
        self.assertContains(self.client.get(reverse("billing:plans") + "?interval=yearly"), "25,000")

    def test_signup_form_creates_the_account_signs_in_and_lands_on_billing(self):
        r = self.client.post(reverse("billing:signup"), {
            "company_name": "Web Co", "owner_name": "Web Owner", "email": "web@example.com", "phone": "0300", "password1": PASSWORD, "password2": PASSWORD,
            "plan": self.starter.pk, "interval": "monthly", "accept": "on"})
        self.assertRedirects(r, reverse("billing:home"), fetch_redirect_response=False)
        sub = Subscription.objects.get(company_name="Web Co")
        self.assertEqual(sub.status, Subscription.PENDING)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertRedirects(self.client.get(reverse("dashboard:home")), reverse("billing:home"), fetch_redirect_response=False)

    def test_signup_with_demo_days_lands_on_the_dashboard(self):
        s = PlatformSettings.get()
        s.trial_days = 10
        s.save()
        r = self.client.post(reverse("billing:signup"), {
            "company_name": "Demo Co", "owner_name": "Demo Owner", "email": "demo@example.com", "password1": PASSWORD, "password2": PASSWORD,
            "plan": self.starter.pk, "interval": "monthly", "accept": "on"})
        self.assertRedirects(r, reverse("dashboard:home"), fetch_redirect_response=False)

    def test_signup_validates(self):
        r = self.client.post(reverse("billing:signup"), {"company_name": "X", "owner_name": "Y", "email": "bad", "password1": "a", "password2": "b",
                                                         "plan": self.starter.pk, "interval": "monthly"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Company.objects.count(), 0)

    def test_an_existing_email_is_told_to_sign_in(self):
        signup("First", "dup@example.com", self.starter)
        r = self.client.post(reverse("billing:signup"), {
            "company_name": "Second", "owner_name": "S", "email": "dup@example.com", "password1": PASSWORD, "password2": PASSWORD,
            "plan": self.starter.pk, "interval": "monthly", "accept": "on"})
        self.assertContains(r, "Sign in instead")

    def test_closed_signup(self):
        s = PlatformSettings.get()
        s.allow_public_signup = False
        s.save()
        r = self.client.post(reverse("billing:signup"), {"company_name": "X"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Company.objects.count(), 0)

    def test_signed_in_people_are_sent_to_billing_from_signup_and_plans(self):
        _, user, _ = signup("In Co", "in@example.com", self.starter)
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse("billing:signup")), reverse("billing:home"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse("billing:plans")), reverse("billing:home"), fetch_redirect_response=False)

    def test_normal_sign_in_works_for_a_pending_company(self):
        _, user, _ = signup("Pw Co", "pw@example.com", self.starter)
        r = self.client.post(reverse("accounts:login"), {"username": user.username, "password": PASSWORD})
        self.assertEqual(r.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)


class CompanyBillingPageTests(BillingBase):
    def setUp(self):
        self.company, self.user, self.sub = signup("Page Co", "page@example.com", self.starter)
        pay(self.sub)
        self.client.force_login(self.user)

    def test_billing_page_shows_plan_usage_and_month_wise_history(self):
        owner.open_invoice(self.sub, self.pro, "monthly")
        r = self.client.get(reverse("billing:home"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Starter")
        self.assertContains(r, "Invoice history")
        self.assertContains(r, timezone.localtime().strftime("%B %Y"))
        self.assertContains(r, "SUB-")
        self.assertContains(r, "Users")

    def test_manual_payment_form_submits_a_receipt_and_alerts_the_owners(self):
        make_owner()
        inv = owner.open_invoice(self.sub)
        r = self.client.post(reverse("billing:pay_manual", args=[inv.pk]), {"method": "easypaisa", "reference": "EP-77"})
        self.assertRedirects(r, reverse("billing:home"))
        pay_row = Payment.objects.get(invoice=inv, reference="EP-77")
        self.assertEqual((pay_row.status, pay_row.submitted_by_name, pay_row.company_id), (Payment.PENDING, self.user.username, self.company.pk))
        self.assertTrue(any("Payment to review" in m.subject for m in mail.outbox))

    def test_receipt_or_reference_is_required(self):
        inv = owner.open_invoice(self.sub)
        self.client.post(reverse("billing:pay_manual", args=[inv.pk]), {"method": "bank"})
        self.assertFalse(Payment.objects.filter(invoice=inv).exists())

    def test_cannot_touch_another_companys_invoice(self):
        _, _, other_sub = signup("Other Co", "other@example.com", self.starter)
        inv = owner.open_invoice(other_sub)
        self.assertEqual(self.client.post(reverse("billing:pay_manual", args=[inv.pk]), {"method": "bank", "reference": "x"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("billing:invoice", args=[inv.pk])).status_code, 404)

    def test_invoice_print_page(self):
        inv = owner.open_invoice(self.sub)
        self.assertContains(self.client.get(reverse("billing:invoice", args=[inv.pk])), inv.number)

    def test_change_plan_makes_an_invoice(self):
        r = self.client.post(reverse("billing:change_plan"), {"plan": self.pro.pk, "interval": "yearly"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Invoice.objects.filter(company_id=self.company.pk, plan=self.pro, interval="yearly", status=Invoice.OPEN).exists())

    def test_cancel_and_resume(self):
        self.client.post(reverse("billing:cancel"))
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.cancel_at_period_end)
        self.client.post(reverse("billing:cancel"), {"action": "resume"})
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.cancel_at_period_end)

    def test_staff_without_billing_permission_cannot_pay(self):
        staff = User.objects.create_user("clerk", "clerk@example.com", PASSWORD, store=self.user.store)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(reverse("billing:home")).status_code, 402)
        inv = owner.open_invoice(self.sub)
        self.assertEqual(self.client.post(reverse("billing:pay_manual", args=[inv.pk]), {"method": "bank", "reference": "x"}).status_code, 403)

    def test_banner_counts_down_the_demo_or_the_period(self):
        self.sub.current_period_end = timezone.now() + timedelta(days=3, hours=1)
        self.sub.save()
        self.assertContains(self.client.get(reverse("dashboard:home")), "renews in 3 days")

    def test_sidebar_has_billing_and_no_owner_console_link(self):
        r = self.client.get(reverse("dashboard:home"))
        self.assertContains(r, 'href="/billing/"')
        self.assertNotContains(r, "/platform/")
        self.client.force_login(self.erp_admin)
        self.assertNotContains(self.client.get(reverse("dashboard:home")), 'href="/platform/"')

    def test_sidebar_plan_card_shows_plan_state_usage_and_upgrade(self):
        r = self.client.get(reverse("dashboard:home"))
        self.assertContains(r, "sbp-head")
        self.assertContains(r, "Starter plan")
        self.assertContains(r, "Active")
        self.assertContains(r, "Upgrade plan")
        self.assertContains(r, 'href="/billing/#plans"')
        self.assertContains(r, "Billing &amp; plan")
        self.assertEqual(r.context["billing_plan"]["name"], "Starter")
        self.assertTrue(r.context["billing_plan"]["usage"])          # Starter has limits, so the mini meters show

    def test_plan_card_without_billing_permission_has_no_buttons_or_numbers(self):
        staff = User.objects.create_user("clerk2", "clerk2@example.com", PASSWORD, store=self.user.store)
        self.client.force_login(staff)
        r = self.client.get(reverse("dashboard:home"))
        card = r.context["billing_plan"]
        self.assertFalse(card["can_view"] or card["can_upgrade"])
        self.assertEqual(card["usage"], [])
        self.assertNotContains(r, "Upgrade plan")
        self.assertNotContains(r, "Billing &amp; plan")

    def test_plan_card_unpaid_company_is_asked_to_pay(self):
        company, user, sub = signup("Unpaid Co", "unpaid@example.com", self.starter)
        self.client.force_login(user)
        r = self.client.get(reverse("billing:home"))
        card = r.context["billing_plan"]
        self.assertEqual((card["state"], card["locked"], card["pct"]), ("pending", True, 0))
        self.assertContains(r, "Pay now")
        self.assertContains(r, "Pay your first invoice to start")

    def test_owner_announcement_shows_on_every_erp_page_until_switched_off(self):
        s = PlatformSettings.get()
        s.announcement_enabled, s.announcement_text, s.announcement_level = True, "Maintenance tonight 11pm", "warning"
        s.save()
        page = self.client.get(reverse("dashboard:home"))
        self.assertContains(page, "Maintenance tonight 11pm")
        self.assertContains(page, "bp-banner warning")
        s.announcement_enabled = False
        s.save()
        self.assertNotContains(self.client.get(reverse("dashboard:home")), "Maintenance tonight 11pm")

    def test_the_old_platform_url_is_gone_from_the_erp(self):
        self.assertEqual(self.client.get("/platform/").status_code, 404)


# ------------------------------------------------------------------------------------------------------------------ gateways
class StripeTests(BillingBase):
    def setUp(self):
        self.company, self.user, self.sub = signup("Card Co", "card@example.com", self.starter)
        s = PlatformSettings.get()
        s.stripe_enabled, s.stripe_secret_key, s.stripe_webhook_secret = True, "sk_test_x", "whsec_test"
        s.save()
        self.inv = owner.open_invoice(self.sub)
        self.pay = Payment.objects.create(invoice=self.inv, company_id=self.company.pk, amount=self.inv.total, method=Payment.STRIPE,
                                          stripe_session_id="cs_test_1")

    def _signed(self, payload, secret="whsec_test", ts=None):
        body = json.dumps(payload).encode()
        ts = ts or int(time.time())
        sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        return body, f"t={ts},v1={sig}"

    def test_signed_webhook_marks_the_invoice_paid_and_opens_the_erp(self):
        body, header = self._signed({"type": "checkout.session.completed",
                                     "data": {"object": {"id": "cs_test_1", "payment_status": "paid", "payment_intent": "pi_1"}}})
        r = self.client.post(reverse("billing:stripe_webhook"), body, content_type="application/json", HTTP_STRIPE_SIGNATURE=header)
        self.assertEqual(r.status_code, 200)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Invoice.PAID)
        self.pay.refresh_from_db()
        self.assertEqual((self.pay.status, self.pay.stripe_payment_intent), (Payment.SUCCEEDED, "pi_1"))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "active")

    def test_webhook_twice_is_harmless(self):
        body, header = self._signed({"type": "checkout.session.completed", "data": {"object": {"id": "cs_test_1", "payment_status": "paid"}}})
        for _ in range(2):
            self.client.post(reverse("billing:stripe_webhook"), body, content_type="application/json", HTTP_STRIPE_SIGNATURE=header)
        self.assertEqual(Invoice.objects.filter(status=Invoice.PAID).count(), 1)

    def test_wrong_signature_is_rejected_and_nothing_changes(self):
        body, header = self._signed({"type": "checkout.session.completed", "data": {"object": {"id": "cs_test_1", "payment_status": "paid"}}}, secret="other")
        r = self.client.post(reverse("billing:stripe_webhook"), body, content_type="application/json", HTTP_STRIPE_SIGNATURE=header)
        self.assertEqual(r.status_code, 400)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Invoice.OPEN)

    def test_old_webhook_is_rejected(self):
        body, header = self._signed({"type": "x", "data": {"object": {}}}, ts=int(time.time()) - 4000)
        with self.assertRaises(stripe_gateway.StripeError):
            stripe_gateway.verify_webhook(body, header, "whsec_test")

    def test_unpaid_session_does_not_activate(self):
        body, header = self._signed({"type": "checkout.session.completed", "data": {"object": {"id": "cs_test_1", "payment_status": "unpaid"}}})
        self.client.post(reverse("billing:stripe_webhook"), body, content_type="application/json", HTTP_STRIPE_SIGNATURE=header)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Invoice.OPEN)

    def test_pay_button_creates_checkout_and_return_page_confirms_with_stripe(self):
        self.client.force_login(self.user)
        fake_session = {"id": "cs_new", "url": "https://checkout.stripe.test/pay"}
        with mock.patch("billing.views_company.stripe_gateway.create_checkout_session", return_value=fake_session) as create:
            r = self.client.post(reverse("billing:pay_stripe", args=[self.inv.pk]))
        self.assertRedirects(r, "https://checkout.stripe.test/pay", fetch_redirect_response=False)
        self.assertTrue(create.called)
        self.assertTrue(Payment.objects.filter(stripe_session_id="cs_new", status=Payment.PENDING, submitted_by_name=self.user.username).exists())
        with mock.patch("billing.views_company.stripe_gateway.retrieve_session", return_value={"id": "cs_new", "payment_status": "paid", "payment_intent": "pi_9"}):
            self.client.get(reverse("billing:stripe_return") + "?session_id=cs_new")
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Invoice.PAID)
        self.assertEqual(self.client.get(reverse("catalog:product_list")).status_code, 200)

    def test_card_payment_is_refused_when_stripe_is_off(self):
        s = PlatformSettings.get()
        s.stripe_enabled = False
        s.save()
        self.client.force_login(self.user)
        r = self.client.post(reverse("billing:pay_stripe", args=[self.inv.pk]))
        self.assertRedirects(r, reverse("billing:home"))

    def test_amount_minor_units(self):
        self.assertEqual(stripe_gateway.amount_minor(Decimal("2500.50"), "PKR"), 250050)
        self.assertEqual(stripe_gateway.amount_minor(Decimal("1000"), "JPY"), 1000)


class GoogleTests(BillingBase):
    def info(self, **over):
        base = {"aud": "cid", "iss": "https://accounts.google.com", "exp": str(int(time.time()) + 600), "email_verified": "true",
                "sub": "g-1", "email": "Gina@Example.com", "name": "Gina G"}
        base.update(over)
        return base

    def test_token_checks(self):
        ok = verify_id_token("tok", "cid", fetch=lambda t: self.info())
        self.assertEqual(ok["email"], "gina@example.com")
        for bad in ({"aud": "other"}, {"iss": "evil.com"}, {"exp": "1"}, {"email_verified": "false"}):
            with self.assertRaises(GoogleAuthError):
                verify_id_token("tok", "cid", fetch=lambda t, b=bad: self.info(**b))
        with self.assertRaises(GoogleAuthError):
            verify_id_token("tok", "", fetch=lambda t: self.info())

    def _post(self, data):
        return self.client.post(reverse("billing:google_auth"), json.dumps(data), content_type="application/json")

    def setUp(self):
        s = PlatformSettings.get()
        s.google_client_id = "cid"
        s.save()

    def test_signup_with_google_creates_company_logs_in_and_waits_for_payment(self):
        with mock.patch("billing.views_public.verify_id_token", return_value={"sub": "g-1", "email": "gina@example.com", "name": "Gina G", "picture": ""}):
            r = self._post({"credential": "x", "mode": "signup", "plan": self.pro.pk, "interval": "yearly", "company_name": "Gina Mart"})
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["redirect"], reverse("billing:home"))
        sub = Subscription.objects.get(company_name="Gina Mart")
        self.assertEqual((sub.plan, sub.interval, sub.state()), (self.pro, "yearly", "pending"))
        self.assertIn("_auth_user_id", self.client.session)
        self.assertRedirects(self.client.get(reverse("dashboard:home")), reverse("billing:home"), fetch_redirect_response=False)

    def test_signup_with_google_is_refused_when_sign_up_is_closed(self):
        s = PlatformSettings.get()
        s.allow_public_signup = False
        s.save()
        with mock.patch("billing.views_public.verify_id_token", return_value={"sub": "g-5", "email": "late@example.com", "name": "", "picture": ""}):
            r = self._post({"credential": "x", "mode": "signup"})
        self.assertEqual(r.status_code, 403)

    def test_signin_with_unknown_google_account_points_to_signup(self):
        with mock.patch("billing.views_public.verify_id_token", return_value={"sub": "g-2", "email": "nobody@example.com", "name": "", "picture": ""}):
            r = self._post({"credential": "x", "mode": "signin"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["signup_url"], reverse("billing:signup"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_signin_with_google_for_existing_user_links_identity(self):
        company, user, _ = signup("Linked", "linked@example.com", self.starter)
        with mock.patch("billing.views_public.verify_id_token", return_value={"sub": "g-3", "email": "linked@example.com", "name": "", "picture": ""}):
            r = self._post({"credential": "x", "mode": "signin"})
        self.assertTrue(r.json()["ok"])
        self.assertEqual(user.google_identity.sub, "g-3")

    def test_bad_token_is_refused(self):
        with mock.patch("billing.views_public.verify_id_token", side_effect=GoogleAuthError("nope")):
            r = self._post({"credential": "x", "mode": "signin"})
        self.assertEqual(r.status_code, 400)


# ------------------------------------------------------------------------------------------------------------------ tenants
class TenantScopingTests(BillingBase):
    def setUp(self):
        self.a, self.ua, self.suba = signup("Alpha", "alpha@example.com", self.pro)
        self.b, self.ub, self.subb = signup("Beta", "beta@example.com", self.pro)
        pay(self.suba)
        pay(self.subb)
        User.objects.create_user("beta2", "beta2@example.com", PASSWORD, store=self.ub.store)

    def _req(self, user):
        req = RequestFactory().get("/")
        req.user, req.store = user, user.store
        req.company = user.store.company if user.store_id else None
        return req

    def test_scope_queryset_limits_rows_to_own_company(self):
        rows = scope_queryset(self._req(self.ua), User.objects.all())
        self.assertEqual({u.pk for u in rows}, {self.ua.pk})
        self.assertEqual(set(scope_queryset(self._req(self.ua), Company.objects.all())), {self.a})
        self.assertEqual(scope_queryset(self._req(self.erp_admin), User.objects.all()).count(), User.objects.count())

    def test_scoping_is_off_for_a_single_company_install(self):
        with mock.patch("billing.tenancy.multi_company", return_value=False):
            self.assertEqual(scope_queryset(self._req(self.ua), Company.objects.all()).count(), 2)
            self.assertEqual(scope_queryset(self._req(self.ua), User.objects.all()).count(), User.objects.count())

    def test_staff_list_shows_only_my_companys_people(self):
        self.client.force_login(self.ub)
        r = self.client.get(reverse("accounts:staff_user_list"))
        self.assertContains(r, "beta2")
        self.assertNotContains(r, "alpha@example.com")

    def test_cannot_open_another_companys_user(self):
        self.client.force_login(self.ua)
        self.assertEqual(self.client.get(reverse("accounts:staff_user_edit", args=[self.ub.pk])).status_code, 404)

    def test_company_admins_cannot_edit_shared_roles_when_there_are_several_companies(self):
        self.client.force_login(self.ua)
        self.assertEqual(self.client.get(reverse("accounts:role_add")).status_code, 403)
        self.client.force_login(self.erp_admin)
        self.assertEqual(self.client.get(reverse("accounts:role_add")).status_code, 200)

    def test_erp_screens_only_list_my_companys_records(self):
        self.client.force_login(self.ua)
        r = self.client.get(reverse("erp_ui:list", args=["company", "company"]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Alpha")
        self.assertNotContains(r, "Beta")


class BridgeTests(BillingBase):
    def test_admins_and_notifications(self):
        company, user, sub = signup("Bridge Co", "bridge@example.com", self.starter)
        self.assertEqual(bridge.admin_emails(company.pk), ["bridge@example.com"])
        owner.notify_company(sub, "Hello", "World", key="k1")
        owner.notify_company(sub, "Hello", "World", key="k1")                     # same key: once
        self.assertEqual(Notification.objects.filter(user=user, title="Hello").count(), 1)

    def test_company_info_and_unmanaged_list(self):
        company, _, sub = signup("Info Co", "info@example.com", self.starter)
        self.assertEqual(bridge.company_info(company.pk)["name"], "Info Co")
        self.assertIsNone(bridge.company_info(999999))
        legacy = Company.objects.create(name="Legacy Co")
        self.assertEqual([c.pk for c in bridge.companies_without_subscription([sub.company_id])], [legacy.pk])
        self.assertEqual(bridge.user_counts([company.pk]), {company.pk: 1})

    def test_reminders_reach_the_company_admins_in_the_erp(self):
        company, user, sub = signup("Remind Co", "remind@example.com", self.starter)
        pay(sub)
        end = sub.current_period_end
        for days in (7, 3, 1):
            now = end - timedelta(days=days, hours=1) + timedelta(minutes=30)
            owner.refresh_subscription(sub, now)
            owner.refresh_subscription(sub, now)                                           # a second run must not repeat it
        titles = list(Notification.objects.filter(user=user).values_list("title", flat=True))
        self.assertEqual(sum("ends in" in t for t in titles), 3, titles)

    def test_pending_company_gets_nudged_to_pay(self):
        company, user, sub = signup("Nudge Co", "nudge@example.com", self.starter)
        owner.refresh_subscription(sub, timezone.now() + timedelta(days=3))
        self.assertTrue(Notification.objects.filter(user=user, title__icontains="Finish setting up").exists())
