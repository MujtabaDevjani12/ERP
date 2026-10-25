"""
The owner console: its own sign-in (not Django users), forgot / reset password, e-mail, the dashboard, companies, plans, payments,
invoices, settings, team and the commands that set it up. Everything here runs with the console's own URLconf, middleware and
template settings (config.owner_settings), exactly like the second website does.
"""
import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.testkit import BillingBase, OWNER_PASSWORD, User, make_owner, make_plans, pay, signup
from company.models import Company
from config import owner_settings
from core.errors import ERPError
from core.pos_fixtures import PASSWORD
from notifications.models import Notification
from ownerconsole import auth, emailing, services
from ownerconsole.models import BillingEvent, EmailLog, Invoice, OwnerAccount, Payment, PlatformSettings, Plan, Subscription

OWNER_SITE = dict(ROOT_URLCONF="config.owner_urls", MIDDLEWARE=owner_settings.MIDDLEWARE, TEMPLATES=owner_settings.TEMPLATES,
                  MESSAGE_STORAGE=owner_settings.MESSAGE_STORAGE, CSRF_COOKIE_NAME="owner_csrftoken", SESSION_COOKIE_NAME="owner_sessionid")


def sign_in(client, email="boss@owner.test", password=OWNER_PASSWORD, remember=False):
    data = {"email": email, "password": password}
    if remember:
        data["remember"] = "on"
    return client.post(reverse("owner:login"), data)


# ------------------------------------------------------------------------------------------------------------------ business rules
class SubscriptionRulesTests(BillingBase):
    def setUp(self):
        self.company, self.user, self.sub = signup("Pay Co", "pay@example.com", self.starter)
        self.boss = make_owner()

    def test_state_machine_pending_trial_active_grace_expired(self):
        sub = self.sub
        self.assertEqual(sub.state(), "pending")
        services.extend_trial(sub, 10, self.boss)
        sub.refresh_from_db()
        self.assertEqual(sub.state(), "trial")
        end = sub.trial_ends_at
        self.assertEqual(sub.state(end - timedelta(days=1)), "trial")
        self.assertEqual(sub.state(end + timedelta(minutes=1)), "expired")          # an unpaid demo has no grace
        pay(sub)
        end = sub.current_period_end
        self.assertEqual(sub.state(end - timedelta(days=1)), "active")
        self.assertEqual(sub.state(end + timedelta(days=2)), "grace")
        self.assertEqual(sub.state(end + timedelta(days=6)), "expired")

    def test_manual_payment_waits_for_approval_then_activates_the_plan(self):
        inv = services.open_invoice(self.sub)
        self.assertEqual(inv.total, Decimal("2500.00"))
        self.assertEqual(inv.pk, self.sub.invoices.first().pk)                        # the signup invoice is re-used
        pay_row = services.submit_manual_payment(inv, user_name="pay", method=Payment.JAZZCASH, reference="TX123")
        self.assertEqual(pay_row.status, Payment.PENDING)
        self.assertEqual(Invoice.objects.get(pk=inv.pk).status, Invoice.OPEN)
        services.approve_payment(pay_row, self.boss)
        inv.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertEqual((inv.status, self.sub.status, self.sub.state()), (Invoice.PAID, Subscription.ACTIVE, "active"))
        self.assertGreaterEqual(self.sub.days_left(), 29)
        pay_row.refresh_from_db()
        self.assertEqual((pay_row.status, pay_row.reviewed_by), (Payment.SUCCEEDED, self.boss))
        services.approve_payment(pay_row, self.boss)                                  # twice is harmless
        self.assertEqual(Invoice.objects.filter(status=Invoice.PAID).count(), 1)
        self.assertTrue(Notification.objects.filter(user=self.user, title__icontains="Payment received").exists())

    def test_rejected_payment_keeps_invoice_open_and_tells_the_company(self):
        inv = services.open_invoice(self.sub)
        pay_row = services.submit_manual_payment(inv, user_name="pay", method=Payment.BANK, reference="BAD")
        services.reject_payment(pay_row, self.boss, "Amount does not match")
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.OPEN)
        self.assertTrue(Notification.objects.filter(user=self.user, title__icontains="could not be verified").exists())
        with self.assertRaises(ERPError):
            services.reject_payment(pay_row, self.boss)                               # already decided

    def test_receipt_or_reference_is_required_and_method_must_be_manual(self):
        inv = services.open_invoice(self.sub)
        with self.assertRaises(ERPError):
            services.submit_manual_payment(inv, user_name="x", method=Payment.BANK)
        with self.assertRaises(ERPError):
            services.submit_manual_payment(inv, user_name="x", method=Payment.STRIPE, reference="r")
        proof = SimpleUploadedFile("r.png", b"\x89PNG\r\n", content_type="image/png")
        with tempfile.TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media):
            self.assertEqual(services.submit_manual_payment(inv, user_name="x", method=Payment.BANK, proof=proof).status, Payment.PENDING)

    def test_a_paid_invoice_cannot_be_paid_again_or_a_void_one_paid_at_all(self):
        inv = services.open_invoice(self.sub)
        services.mark_invoice_paid(inv)
        with self.assertRaises(ERPError):
            services.submit_manual_payment(inv, user_name="x", method=Payment.BANK, reference="late")
        other = services.create_invoice(self.sub)
        services.void_invoice(other, self.boss)
        with self.assertRaises(ERPError):
            services.mark_invoice_paid(other)

    def test_yearly_invoice_extends_twelve_months_and_renewal_stacks(self):
        inv = services.open_invoice(self.sub, interval="yearly")
        self.assertEqual(inv.total, Decimal("25000.00"))
        services.mark_invoice_paid(inv, by=self.boss)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.interval, "yearly")
        self.assertGreaterEqual(self.sub.days_left(), 364)
        first_end = self.sub.current_period_end
        services.mark_invoice_paid(services.open_invoice(self.sub))
        self.sub.refresh_from_db()
        self.assertGreater(self.sub.current_period_end, first_end + timedelta(days=360))

    def test_plan_change_is_invoiced_and_switches_only_after_payment(self):
        pay(self.sub)
        inv = services.request_plan_change(self.sub, self.pro, "monthly", by=self.user)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan, self.starter)
        services.mark_invoice_paid(inv)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan, self.pro)
        self.assertEqual(inv.kind, Invoice.CHANGE)
        with self.assertRaises(ERPError):
            services.request_plan_change(self.sub, Plan.objects.create(name="Old", slug="old", is_active=False), "monthly")

    def test_a_pending_company_can_switch_plan_before_paying(self):
        first = self.sub.invoices.get()
        inv = services.request_plan_change(self.sub, self.pro, "yearly", by=self.user)
        first.refresh_from_db()
        self.assertEqual((first.status, inv.total), (Invoice.VOID, Decimal("75000.00")))
        services.mark_invoice_paid(inv)
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.plan, self.sub.state()), (self.pro, "active"))

    def test_tax_percent_is_added_to_invoices(self):
        s = PlatformSettings.get()
        s.tax_percent = Decimal("10")
        s.save()
        self.sub.invoices.all().delete()
        inv = services.open_invoice(self.sub)
        self.assertEqual((inv.subtotal, inv.tax_amount, inv.total), (Decimal("2500.00"), Decimal("250.00"), Decimal("2750.00")))

    def test_invoice_numbers_are_unique_and_sequential(self):
        a = services.create_invoice(self.sub)
        b = services.create_invoice(self.sub)
        self.assertLess(a.number, b.number)
        self.assertTrue(a.number.startswith(f"SUB-{timezone.now().year}-"))

    def test_suspend_and_unsuspend(self):
        pay(self.sub)
        services.suspend(self.sub, "Chargeback", self.boss)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "suspended")
        self.assertTrue(Notification.objects.filter(user=self.user, title__icontains="suspended").exists())
        services.unsuspend(self.sub, self.boss)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "active")
        self.assertTrue(BillingEvent.objects.filter(company_id=self.company.pk, kind="suspended").exists())

    def test_grant_days_adds_paid_days_and_extend_trial_on_a_paying_company_does_the_same(self):
        pay(self.sub)
        before = self.sub.current_period_end
        services.grant_days(self.sub, 10, self.boss)
        services.extend_trial(self.sub, 5, self.boss)
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.current_period_end - before).days, 15)

    def test_grant_days_opens_a_pending_company_as_active(self):
        services.grant_days(self.sub, 30, self.boss)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "active")

    def test_owner_set_plan_resets_nothing_but_the_plan(self):
        services.owner_set_plan(self.sub, self.pro, "yearly", self.boss, price_override=Decimal("50000"))
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.plan, self.sub.interval, self.sub.price()), (self.pro, "yearly", Decimal("50000.00")))


class CycleAndReminderTests(BillingBase):
    def test_cycle_creates_the_renewal_invoice_before_expiry_and_expires_unpaid_accounts(self):
        _, _, sub = signup("Cycle Co", "cycle@example.com", self.starter)
        pay(sub)
        near_end = sub.current_period_end - timedelta(days=3)
        res = services.refresh_subscription(sub, near_end)
        self.assertTrue(res["invoice"])
        self.assertEqual(sub.invoices.filter(status=Invoice.OPEN).count(), 1)
        services.refresh_subscription(sub, near_end)                                  # not a second one
        self.assertEqual(sub.invoices.filter(status=Invoice.OPEN).count(), 1)
        services.sync_status(sub, sub.current_period_end + timedelta(days=10))
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.EXPIRED)

    def test_cancel_at_period_end_stops_renewal(self):
        _, user, sub = signup("Cancel Co", "cancel@example.com", self.starter)
        pay(sub)
        services.cancel_at_period_end(sub, user)
        sub.refresh_from_db()
        res = services.refresh_subscription(sub, sub.current_period_end - timedelta(days=2))
        self.assertFalse(res["invoice"])
        services.resume(sub, user)
        sub.refresh_from_db()
        self.assertFalse(sub.cancel_at_period_end)

    def test_pending_company_without_an_invoice_gets_one(self):
        _, _, sub = signup("No Inv", "noinv@example.com", self.starter)
        sub.invoices.all().delete()
        self.assertTrue(services.refresh_subscription(sub)["invoice"])
        self.assertEqual(sub.invoices.count(), 1)

    def test_expired_notice_is_sent_once(self):
        _, user, sub = signup("Late Co", "late@example.com", self.starter)
        pay(sub)
        late = sub.current_period_end + timedelta(days=20)
        services.refresh_subscription(sub, late)
        services.refresh_subscription(sub, late)
        self.assertEqual(Notification.objects.filter(user=user, title__icontains="expired").count(), 1)

    def test_run_cycle_command_and_totals(self):
        signup("Cmd Co", "cmd@example.com", self.starter)
        out = StringIO()
        call_command("run_billing_cycle", stdout=out)
        totals = services.run_cycle()
        self.assertEqual(totals["checked"], 1)

    def test_month_end_dates(self):
        from datetime import datetime
        d = timezone.make_aware(datetime(2027, 1, 31, 10))
        self.assertEqual(services.add_months(d, 1).day, 28)
        self.assertEqual(services.period_end(d, "yearly").year, 2028)

    def test_metrics_and_revenue_bars(self):
        _, _, sub = signup("Metric Co", "m@example.com", self.pro, "yearly")
        _, _, pending = signup("Pending Co", "p@example.com", self.starter)
        pay(sub)
        m = services.platform_metrics()
        self.assertEqual(m["companies"], 2)
        self.assertEqual((m["counts"]["active"], m["counts"]["pending"], m["locked"]), (1, 1, 1))
        self.assertEqual(m["mrr"], Decimal("6250.00"))
        self.assertEqual(m["total_revenue"], Decimal("75000.00"))
        self.assertEqual(m["outstanding"], Decimal("2500.00"))
        bars = services.revenue_by_month(12)
        self.assertEqual((len(bars), bars[-1][2]), (12, Decimal("75000.00")))


class SeedPlansTests(TestCase):
    def test_seed_plans_is_repeatable(self):
        call_command("seed_plans", verbosity=0)
        call_command("seed_plans", verbosity=0)
        self.assertEqual(Plan.objects.count(), 3)
        self.assertTrue(Plan.objects.get(slug="business").is_featured)


# ------------------------------------------------------------------------------------------------------------------ e-mail
class EmailTests(BillingBase):
    def test_messages_are_logged_and_sent_once_per_key(self):
        a = emailing.send_message("a@example.com", "Hi", "Hi", ["Body line"], button=("Go", "http://x/y"), kind="t", key="k")
        b = emailing.send_message("a@example.com", "Hi", "Hi", ["Body line"], kind="t", key="k")
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("http://x/y", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].alternatives[0][1], "text/html")
        self.assertEqual(EmailLog.objects.filter(status=EmailLog.SENT).count(), 1)

    def test_sender_uses_the_saved_business_and_address(self):
        s = PlatformSettings.get()
        s.business_name, s.email_from_address, s.email_reply_to = "Acme ERP", "billing@acme.test", "help@acme.test"
        s.save()
        emailing.send_message("c@example.com", "Hello", "Hello", ["x"])
        self.assertIn("billing@acme.test", mail.outbox[0].from_email)
        self.assertIn("Acme ERP", mail.outbox[0].from_email)
        self.assertEqual(mail.outbox[0].reply_to, ["help@acme.test"])

    def test_a_failing_mail_server_never_breaks_the_caller_but_is_logged(self):
        from unittest import mock
        with mock.patch("ownerconsole.emailing.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            log = emailing.send_message("x@example.com", "S", "S", ["b"])
            _, _, sub = signup("Safe Co", "safe@example.com", self.starter)                # signup still works
        self.assertEqual(log.status, EmailLog.FAILED)
        self.assertIn("smtp down", log.error)
        self.assertEqual(sub.state(), "pending")
        with mock.patch("ownerconsole.emailing.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            with self.assertRaises(OSError):
                emailing.send_message("x@example.com", "S", "S", ["b"], raise_errors=True)

    def test_owner_alert_respects_the_switches(self):
        make_owner()
        make_owner("off@owner.test", name="Off").__class__.objects.filter(email="off@owner.test").update(notify_email=False)
        self.assertEqual(emailing.owner_recipients(), ["boss@owner.test"])
        s = PlatformSettings.get()
        s.email_owners = False
        s.save()
        self.assertEqual(emailing.owner_recipients(), [])

    def test_smtp_connection_uses_saved_details(self):
        s = PlatformSettings.get()
        s.email_host, s.email_port, s.email_username, s.email_password = "smtp.example.com", 2525, "u", "p"
        s.save()
        conn = emailing.connection_for(s)
        self.assertEqual((conn.host, conn.port, conn.username, conn.use_tls), ("smtp.example.com", 2525, "u", True))

    def test_links_need_the_erp_address(self):
        self.assertEqual(emailing.link("/billing/"), "")
        s = PlatformSettings.get()
        s.erp_base_url = "https://erp.test/"
        s.save()
        self.assertEqual(emailing.link("/billing/"), "https://erp.test/billing/")


# ------------------------------------------------------------------------------------------------------------------ sign in
@override_settings(**OWNER_SITE)
class AuthTests(TestCase):
    def setUp(self):
        self.boss = make_owner()

    def test_every_page_needs_sign_in(self):
        for name in ("dashboard", "companies", "plans", "payments", "invoices", "activity", "emails", "settings", "email_settings", "team", "account"):
            r = self.client.get(reverse(f"owner:{name}"))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn(reverse("owner:login"), r["Location"], name)
        self.assertEqual(self.client.post(reverse("owner:run_cycle")).status_code, 302)

    def test_login_page_renders(self):
        r = self.client.get(reverse("owner:login"))
        self.assertContains(r, "Forgot password")
        self.assertNotContains(r, "Sign up")

    def test_sign_in_sets_one_signed_cookie_and_opens_the_dashboard(self):
        r = sign_in(self.client)
        self.assertRedirects(r, reverse("owner:dashboard"), fetch_redirect_response=False)
        self.assertIn("owner_auth", r.cookies)
        self.assertTrue(r.cookies["owner_auth"]["httponly"])
        self.assertEqual(self.client.get(reverse("owner:dashboard")).status_code, 200)
        self.boss.refresh_from_db()
        self.assertIsNotNone(self.boss.last_login)

    def test_remember_me_makes_a_long_lived_cookie(self):
        r = sign_in(self.client, remember=True)
        self.assertEqual(int(r.cookies["owner_auth"]["max-age"]), 14 * 86400)
        self.client.cookies.clear()
        r = sign_in(self.client)
        self.assertEqual(r.cookies["owner_auth"]["max-age"], "")

    def test_wrong_password_and_unknown_email_look_the_same(self):
        a = sign_in(self.client, password="wrong-password-123")
        b = sign_in(self.client, email="nobody@owner.test", password="wrong-password-123")
        self.assertContains(a, "e-mail or password is not correct")
        self.assertContains(b, "e-mail or password is not correct")
        self.assertNotIn("owner_auth", a.cookies)

    def test_five_wrong_passwords_lock_the_account_even_for_the_right_one(self):
        for _ in range(auth.MAX_ATTEMPTS):
            sign_in(self.client, password="wrong-password-123")
        self.boss.refresh_from_db()
        self.assertTrue(self.boss.is_locked)
        r = sign_in(self.client)
        self.assertContains(r, "Too many wrong attempts")
        self.boss.locked_until = timezone.now() - timedelta(minutes=1)
        self.boss.save()
        self.assertEqual(sign_in(self.client).status_code, 302)

    def test_switched_off_account_cannot_sign_in(self):
        self.boss.is_active = False
        self.boss.save()
        self.assertContains(sign_in(self.client), "switched off")

    def test_signing_out_and_a_switched_off_account_lose_access(self):
        sign_in(self.client)
        self.boss.is_active = False
        self.boss.save()
        self.assertEqual(self.client.get(reverse("owner:dashboard")).status_code, 302)
        self.boss.is_active = True
        self.boss.save()
        self.assertEqual(self.client.get(reverse("owner:dashboard")).status_code, 200)
        self.client.post(reverse("owner:logout"))
        self.assertEqual(self.client.get(reverse("owner:dashboard")).status_code, 302)

    def test_logout_needs_post(self):
        sign_in(self.client)
        self.assertEqual(self.client.get(reverse("owner:logout")).status_code, 405)

    def test_a_tampered_cookie_is_rejected(self):
        sign_in(self.client)
        self.client.cookies["owner_auth"] = self.client.cookies["owner_auth"].value[:-3] + "abc"
        self.assertEqual(self.client.get(reverse("owner:dashboard")).status_code, 302)

    def test_an_erp_login_is_not_an_owner_login(self):
        erp_user = User.objects.create_user("erpuser", "erpuser@example.com", PASSWORD)
        self.client.force_login(erp_user)
        self.assertEqual(self.client.get(reverse("owner:dashboard")).status_code, 302)
        self.assertEqual(sign_in(self.client, email="erpuser@example.com", password=PASSWORD).status_code, 200)

    def test_next_is_followed_only_for_local_urls(self):
        r = self.client.post(reverse("owner:login") + "?next=" + reverse("owner:plans"), {"email": "boss@owner.test", "password": OWNER_PASSWORD})
        self.assertRedirects(r, reverse("owner:plans"), fetch_redirect_response=False)
        self.client.cookies.clear()
        r = self.client.post(reverse("owner:login") + "?next=https://evil.test/", {"email": "boss@owner.test", "password": OWNER_PASSWORD})
        self.assertRedirects(r, reverse("owner:dashboard"), fetch_redirect_response=False)

    def test_support_accounts_cannot_open_admin_pages(self):
        make_owner("help@owner.test", role=OwnerAccount.SUPPORT, name="Help")
        sign_in(self.client, "help@owner.test")
        for name in ("dashboard", "companies", "payments", "invoices", "activity", "emails", "account"):
            self.assertEqual(self.client.get(reverse(f"owner:{name}")).status_code, 200, name)
        for name in ("plans", "settings", "email_settings", "team", "plan_add", "team_add"):
            self.assertRedirects(self.client.get(reverse(f"owner:{name}")), reverse("owner:dashboard"), fetch_redirect_response=False, msg_prefix=name)
        self.assertRedirects(self.client.post(reverse("owner:run_cycle")), reverse("owner:dashboard"), fetch_redirect_response=False)


@override_settings(**OWNER_SITE)
class ForgotPasswordTests(TestCase):
    def setUp(self):
        self.boss = make_owner()

    def _link(self):
        body = mail.outbox[-1].body
        return next(line.split(": ", 1)[1].strip() for line in body.splitlines() if "/reset/" in line)

    def test_forgot_page_gives_the_same_answer_for_any_address(self):
        a = self.client.post(reverse("owner:forgot"), {"email": "boss@owner.test"})
        n = len(mail.outbox)
        b = self.client.post(reverse("owner:forgot"), {"email": "ghost@owner.test"})
        self.assertContains(a, "If an owner account uses that address")
        self.assertContains(b, "If an owner account uses that address")
        self.assertEqual(n, 1)
        self.assertEqual(len(mail.outbox), 1)                                          # nothing sent to the unknown address

    def test_the_link_in_the_mail_sets_a_new_password_once(self):
        self.client.post(reverse("owner:forgot"), {"email": "boss@owner.test"})
        link = self._link()
        self.assertEqual(self.client.get(link).status_code, 200)
        r = self.client.post(link, {"password1": "A-brand-new-pw-2026", "password2": "A-brand-new-pw-2026"})
        self.assertRedirects(r, reverse("owner:login"), fetch_redirect_response=False)
        self.assertEqual(sign_in(self.client, password="A-brand-new-pw-2026").status_code, 302)
        self.client.cookies.clear()
        self.assertEqual(sign_in(self.client, password=OWNER_PASSWORD).status_code, 200)       # old password is gone
        self.assertEqual(self.client.get(link).status_code, 400)                               # link is single-use
        self.assertTrue(any("password was changed" in m.subject for m in mail.outbox))

    def test_reset_clears_a_lock(self):
        for _ in range(auth.MAX_ATTEMPTS):
            sign_in(self.client, password="wrong-password-123")
        self.client.post(reverse("owner:forgot"), {"email": "boss@owner.test"})
        self.client.post(self._link(), {"password1": "Another-new-pw-2026", "password2": "Another-new-pw-2026"})
        self.assertEqual(sign_in(self.client, password="Another-new-pw-2026").status_code, 302)

    def test_weak_or_mismatched_passwords_are_refused(self):
        token = auth.make_reset_token(self.boss)
        url = reverse("owner:reset", args=[token])
        for pw, other in (("short", "short"), ("A-brand-new-pw-2026", "different-pw-2026"), (OWNER_PASSWORD, OWNER_PASSWORD), ("1234567890", "1234567890")):
            r = self.client.post(url, {"password1": pw, "password2": other})
            self.assertEqual(r.status_code, 200, pw)
        self.boss.refresh_from_db()
        self.assertTrue(self.boss.check_password(OWNER_PASSWORD))

    def test_bad_expired_and_foreign_tokens(self):
        self.assertEqual(self.client.get(reverse("owner:reset", args=["not-a-token"])).status_code, 400)
        token = auth.make_reset_token(self.boss)
        with self.settings(OWNER_INVITE_HOURS=0):
            from unittest import mock
            with mock.patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 3 * 86400):
                self.assertEqual(self.client.get(reverse("owner:reset", args=[token])).status_code, 400)
        self.boss.is_active = False
        self.boss.save()
        self.assertEqual(self.client.get(reverse("owner:reset", args=[token])).status_code, 400)

    def test_changing_the_password_signs_other_browsers_out(self):
        sign_in(self.client)
        other = self.client_class()
        sign_in(other)
        r = self.client.post(reverse("owner:account"), {"action": "password", "current": OWNER_PASSWORD,
                                                        "password1": "Fresh-password-2026", "password2": "Fresh-password-2026"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get(reverse("owner:dashboard")).status_code, 200)         # this browser stays in
        self.assertEqual(other.get(reverse("owner:dashboard")).status_code, 302)               # the other one is out


# ------------------------------------------------------------------------------------------------------------------ pages
@override_settings(**OWNER_SITE)
class ConsolePagesTests(BillingBase):
    def setUp(self):
        self.boss = make_owner()
        self.company, self.user, self.sub = signup("Console Co", "console@example.com", self.starter)
        sign_in(self.client)

    def test_every_page_opens_in_every_state(self):
        legacy = Company.objects.create(name="Not Billed Yet")
        pay_sub = signup("Paid Co", "paidco@example.com", self.pro)[2]
        pay(pay_sub)
        inv = services.open_invoice(self.sub)
        row = services.submit_manual_payment(inv, user_name="console", method=Payment.BANK, reference="R1")
        services.log_event(self.sub, "note", "hello", "someone")
        pages = [("dashboard", []), ("companies", []), ("company_detail", [self.company.pk]), ("company_detail", [pay_sub.company_id]),
                 ("company_detail", [legacy.pk]), ("plans", []), ("plan_add", []), ("plan_edit", [self.starter.pk]), ("payments", []),
                 ("invoices", []), ("invoice", [inv.pk]), ("activity", []), ("emails", []), ("settings", []), ("email_settings", []),
                 ("team", []), ("team_add", []), ("team_edit", [self.boss.pk]), ("account", []), ("health", [])]
        for name, args in pages:
            r = self.client.get(reverse(f"owner:{name}", args=args))
            self.assertEqual(r.status_code, 200, (name, args))
        for query in ("?status=succeeded", "?status=all", "?state=pending", "?q=Console", "?page=9"):
            self.assertEqual(self.client.get(reverse("owner:payments") + query).status_code, 200, query)
            self.assertEqual(self.client.get(reverse("owner:companies") + query).status_code, 200, query)
            self.assertEqual(self.client.get(reverse("owner:invoices") + query).status_code, 200, query)
        self.assertContains(self.client.get(reverse("owner:companies")), "Console Co")
        self.assertContains(self.client.get(reverse("owner:payments")), "R1")
        self.assertContains(self.client.get(reverse("owner:dashboard")), "Console Co")
        self.assertEqual(self.client.get(reverse("owner:company_detail", args=[99999])).status_code, 404)
        self.assertEqual(self.client.get(reverse("owner:payment_proof", args=[row.pk])).status_code, 404)    # no file attached

    def test_the_console_has_no_erp_pages_and_no_erp_navigation(self):
        for path in ("/catalog/products/", "/billing/", "/accounts/login/", "/platform/", "/signup/"):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        page = self.client.get(reverse("owner:dashboard"))
        self.assertNotContains(page, "/catalog/")
        self.assertContains(page, "Companies")

    def test_dashboard_shows_the_numbers(self):
        pay(self.sub)
        signup("Waiting Co", "waiting@example.com", self.starter)
        r = self.client.get(reverse("owner:dashboard"))
        self.assertEqual(r.context["m"]["counts"]["active"], 1)
        self.assertEqual(r.context["m"]["counts"]["pending"], 1)
        self.assertContains(r, "Waiting Co")
        self.assertEqual(len(r.context["bars"]), 12)

    def test_dashboard_lists_companies_that_have_no_subscription(self):
        Company.objects.create(name="Unbilled Ltd")
        self.assertContains(self.client.get(reverse("owner:dashboard")), "Unbilled Ltd")
        self.assertContains(self.client.get(reverse("owner:companies")), "Unbilled Ltd")

    def test_the_console_keeps_working_when_the_erp_database_cannot_be_read(self):
        from unittest import mock
        with mock.patch("billing.bridge.company_obj", return_value=None):
            self.assertEqual(self.client.get(reverse("owner:company_detail", args=[self.company.pk])).status_code, 200)
            self.assertEqual(self.client.get(reverse("owner:companies")).status_code, 200)

    def test_pages_are_not_cached(self):
        self.assertEqual(self.client.get(reverse("owner:dashboard"))["Cache-Control"], "no-store")


@override_settings(**OWNER_SITE)
class ConsoleActionTests(BillingBase):
    def setUp(self):
        self.boss = make_owner()
        self.company, self.user, self.sub = signup("Action Co", "action@example.com", self.starter)
        sign_in(self.client)

    def test_owner_creates_edits_and_removes_plans(self):
        data = {"name": "Gold", "slug": "gold", "tagline": "All in", "description": "", "price_monthly": "9000", "price_yearly": "90000", "currency": "PKR",
                "modules": ["pos", "hr"], "max_users": "5", "max_stores": "2", "max_products": "0", "max_monthly_sales": "0", "features": "Phone support\nOnboarding",
                "trial_days": "", "is_active": "on", "is_public": "on", "is_featured": "on", "sort_order": "5"}
        self.assertRedirects(self.client.post(reverse("owner:plan_add"), data), reverse("owner:plans"))
        gold = Plan.objects.get(slug="gold")
        self.assertEqual(gold.module_keys, ["pos", "hr"])
        self.assertEqual(gold.price_for("yearly"), Decimal("90000"))
        self.assertEqual(gold.feature_lines, ["Phone support", "Onboarding"])
        self.assertFalse(Plan.objects.get(pk=self.pro.pk).is_featured)                # only one featured plan
        data["price_monthly"] = "9500"
        self.client.post(reverse("owner:plan_edit", args=[gold.pk]), data)
        gold.refresh_from_db()
        self.assertEqual(gold.price_monthly, Decimal("9500"))
        self.client.post(reverse("owner:plan_delete", args=[gold.pk]))
        self.assertFalse(Plan.objects.filter(pk=gold.pk).exists())
        self.client.post(reverse("owner:plan_delete", args=[self.starter.pk]))        # in use: hidden, not deleted
        self.starter.refresh_from_db()
        self.assertFalse(self.starter.is_active or self.starter.is_public)

    def test_plan_form_rejects_negative_prices_and_duplicate_slugs(self):
        base = {"name": "Bad", "slug": "starter", "price_monthly": "-1", "price_yearly": "0", "currency": "PKR", "max_users": "0", "max_stores": "0",
                "max_products": "0", "max_monthly_sales": "0", "sort_order": "1"}
        r = self.client.post(reverse("owner:plan_add"), base)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Plan.objects.filter(name="Bad").count(), 0)

    def test_owner_approves_and_rejects_payments(self):
        inv = services.open_invoice(self.sub)
        row = services.submit_manual_payment(inv, user_name="action", method=Payment.BANK, reference="R2")
        self.client.post(reverse("owner:payment_review", args=[row.pk]), {"action": "approve"})
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.PAID)
        inv2 = services.open_invoice(self.sub, self.pro, "monthly")
        row2 = services.submit_manual_payment(inv2, user_name="action", method=Payment.BANK, reference="R3")
        self.client.post(reverse("owner:payment_review", args=[row2.pk]), {"action": "reject", "note": "No such transfer"})
        row2.refresh_from_db()
        self.assertEqual((row2.status, row2.review_note, row2.reviewed_by), (Payment.REJECTED, "No such transfer", self.boss))
        self.client.post(reverse("owner:payment_review", args=[row2.pk]), {"action": "reject"})        # already decided: a message, no crash
        self.assertEqual(self.client.get(reverse("owner:payment_review", args=[row.pk])).status_code, 405)

    def test_payment_proof_is_served_only_to_a_signed_in_owner(self):
        inv = services.open_invoice(self.sub)
        with tempfile.TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media):
            row = services.submit_manual_payment(inv, user_name="a", method=Payment.BANK, reference="P",
                                                 proof=SimpleUploadedFile("receipt.png", b"\x89PNG\r\n\x1a\nxx", content_type="image/png"))
            r = self.client.get(reverse("owner:payment_proof", args=[row.pk]))
            self.assertEqual((r.status_code, r["Content-Type"]), (200, "image/png"))
            r.close()
            self.client.cookies.clear()
            self.assertEqual(self.client.get(reverse("owner:payment_proof", args=[row.pk])).status_code, 302)

    def test_company_page_actions(self):
        url = reverse("owner:company_detail", args=[self.company.pk])
        self.client.post(url, {"action": "extend_trial", "days": "5"})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "trial")                                    # demo days open a pending company
        self.client.post(url, {"action": "extend_trial", "days": "0"})                 # refused, no crash
        self.client.post(url, {"action": "grant_days", "days": "30"})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "active")
        self.client.post(url, {"action": "suspend", "reason": "Test"})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "suspended")
        self.client.post(url, {"action": "unsuspend"})
        self.client.post(url, {"action": "save", "plan": self.pro.pk, "interval": "yearly", "price_override": "50000", "max_users_override": "9",
                               "max_stores_override": "", "max_products_override": "", "max_monthly_sales_override": "", "notes": "VIP"})
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.plan, self.sub.interval, self.sub.price_override, self.sub.limit("users"), self.sub.notes),
                         (self.pro, "yearly", Decimal("50000.00"), 9, "VIP"))
        self.client.post(url, {"action": "new_invoice"})
        inv = self.sub.invoices.filter(status=Invoice.OPEN).first()
        self.client.post(url, {"action": "mark_paid", "invoice": inv.pk})
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.PAID)
        inv2 = services.create_invoice(self.sub)
        self.client.post(url, {"action": "void", "invoice": inv2.pk})
        inv2.refresh_from_db()
        self.assertEqual(inv2.status, Invoice.VOID)
        self.client.post(url, {"action": "nonsense"})

    def test_owner_can_start_a_subscription_for_a_company_that_has_none(self):
        legacy = Company.objects.create(name="Old Customer", email="old@example.com")
        r = self.client.post(reverse("owner:company_detail", args=[legacy.pk]), {"action": "start", "plan": self.pro.pk, "interval": "monthly", "days": "45"})
        self.assertEqual(r.status_code, 302)
        sub = Subscription.objects.get(company_id=legacy.pk)
        self.assertEqual((sub.company_name, sub.state()), ("Old Customer", "trial"))
        self.assertGreaterEqual(sub.days_left(), 44)
        r = self.client.post(reverse("owner:company_detail", args=[legacy.pk]), {"action": "start", "plan": self.pro.pk})       # already has one
        self.assertEqual(Subscription.objects.filter(company_id=legacy.pk).count(), 1)

    def test_starting_without_demo_days_opens_the_first_invoice(self):
        legacy = Company.objects.create(name="Pay First")
        self.client.post(reverse("owner:company_detail", args=[legacy.pk]), {"action": "start", "plan": self.starter.pk, "interval": "monthly"})
        sub = Subscription.objects.get(company_id=legacy.pk)
        self.assertEqual((sub.state(), sub.invoices.count()), ("pending", 1))

    def test_support_account_cannot_change_a_subscription(self):
        make_owner("help@owner.test", role=OwnerAccount.SUPPORT, name="Help")
        self.client.cookies.clear()
        sign_in(self.client, "help@owner.test")
        self.client.post(reverse("owner:company_detail", args=[self.company.pk]), {"action": "grant_days", "days": "30"})
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "pending")
        row = services.submit_manual_payment(services.open_invoice(self.sub), user_name="a", method=Payment.BANK, reference="S1")
        self.client.post(reverse("owner:payment_review", args=[row.pk]), {"action": "approve"})                         # support may review payments
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.state(), "active")

    def test_settings_keep_secrets_when_left_blank(self):
        s = PlatformSettings.get()
        s.stripe_secret_key, s.stripe_enabled = "sk_live_keep", True
        s.save()
        form = {"business_name": "My ERP", "support_email": "help@erp.test", "support_phone": "", "invoice_footer": "", "erp_base_url": "https://erp.test/",
                "trial_days": "21", "grace_days": "3", "reminder_days": "10, 5 ,1", "renewal_invoice_days": "7", "tax_percent": "0",
                "allow_public_signup": "on", "stripe_enabled": "on", "stripe_publishable_key": "pk_live", "stripe_secret_key": "", "stripe_webhook_secret": "",
                "manual_enabled": "on", "bank_details": "HBL 123", "jazzcash_number": "0300", "easypaisa_number": "", "payment_instructions": "",
                "google_client_id": "cid"}
        self.assertRedirects(self.client.post(reverse("owner:settings"), form), reverse("owner:settings"))
        s = PlatformSettings.get()
        self.assertEqual((s.stripe_secret_key, s.trial_days, s.reminder_days, s.business_name, s.erp_base_url),
                         ("sk_live_keep", 21, "10,5,1", "My ERP", "https://erp.test"))
        for bad in (dict(form, reminder_days="x"), dict(form, erp_base_url="erp.test"), dict(form, manual_enabled="", stripe_enabled="")):
            self.assertEqual(self.client.post(reverse("owner:settings"), bad).status_code, 200)

    def test_google_secret_is_saved_and_never_echoed_or_wiped(self):
        form = {"business_name": "ERP", "support_email": "", "support_phone": "", "invoice_footer": "", "erp_base_url": "", "trial_days": "0", "grace_days": "5",
                "reminder_days": "7,3,1", "renewal_invoice_days": "7", "tax_percent": "0", "allow_public_signup": "on", "manual_enabled": "on",
                "bank_details": "", "jazzcash_number": "", "easypaisa_number": "", "payment_instructions": "",
                "google_client_id": "cid.apps.googleusercontent.com", "google_client_secret": "GOCSPX-secret"}
        self.client.post(reverse("owner:settings"), form)
        self.assertEqual(PlatformSettings.get().google_client_secret, "GOCSPX-secret")
        self.client.post(reverse("owner:settings"), dict(form, google_client_secret=""))
        self.assertEqual(PlatformSettings.get().google_client_secret, "GOCSPX-secret")
        page = self.client.get(reverse("owner:settings"))
        self.assertNotContains(page, "GOCSPX-secret")
        self.assertContains(page, "cid.apps.googleusercontent.com")

    def test_email_settings_save_keep_the_password_and_send_a_test(self):
        form = {"email_host": "smtp.example.com", "email_port": "587", "email_username": "me@example.com", "email_password": "app-pass",
                "email_use_tls": "on", "email_from_name": "Acme", "email_from_address": "billing@example.com", "email_reply_to": "",
                "send_company_emails": "on", "email_owners": "on"}
        self.assertRedirects(self.client.post(reverse("owner:email_settings"), form), reverse("owner:email_settings"))
        self.client.post(reverse("owner:email_settings"), dict(form, email_password=""))
        s = PlatformSettings.get()
        self.assertEqual((s.email_host, s.email_password, s.smtp_ready), ("smtp.example.com", "app-pass", True))
        self.assertNotContains(self.client.get(reverse("owner:email_settings")), "app-pass")
        self.assertEqual(self.client.post(reverse("owner:email_settings"), dict(form, email_use_ssl="on")).status_code, 200)       # TLS + SSL together
        self.assertEqual(self.client.post(reverse("owner:email_settings"), dict(form, email_from_address="", email_username="")).status_code, 200)

    def test_test_email_uses_the_saved_settings_and_reports_failures(self):
        from unittest import mock
        r = self.client.post(reverse("owner:email_settings"), {"action": "test", "to": "me@example.com"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(mail.outbox[-1].to, ["me@example.com"])
        with mock.patch("ownerconsole.emailing.EmailMultiAlternatives.send", side_effect=OSError("Connection refused")):
            r = self.client.post(reverse("owner:email_settings"), {"action": "test", "to": "me@example.com"}, follow=True)
        self.assertContains(r, "could not be sent")
        self.assertTrue(EmailLog.objects.filter(status=EmailLog.FAILED).exists())
        self.assertContains(self.client.get(reverse("owner:emails") + "?status=failed"), "Connection refused")

    def test_run_cycle_button(self):
        self.assertRedirects(self.client.post(reverse("owner:run_cycle")), reverse("owner:dashboard"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("owner:run_cycle")).status_code, 405)

    def test_account_page_updates_the_profile_and_checks_the_current_password(self):
        r = self.client.post(reverse("owner:account"), {"name": "New Name", "email": "boss@owner.test", "phone": "0300", "notify_email": "on"})
        self.assertEqual(r.status_code, 302)
        self.boss.refresh_from_db()
        self.assertEqual(self.boss.name, "New Name")
        r = self.client.post(reverse("owner:account"), {"action": "password", "current": "nope", "password1": "Fresh-password-2026", "password2": "Fresh-password-2026"})
        self.assertEqual(r.status_code, 200)
        make_owner("other@owner.test", name="Other")
        r = self.client.post(reverse("owner:account"), {"name": "X", "email": "other@owner.test"})
        self.assertEqual(r.status_code, 200)


@override_settings(**OWNER_SITE)
class TeamTests(TestCase):
    def setUp(self):
        self.boss = make_owner()
        sign_in(self.client)

    def test_adding_a_member_emails_an_invitation_that_sets_the_first_password(self):
        r = self.client.post(reverse("owner:team_add"), {"name": "Sara", "email": "Sara@Owner.test", "phone": "", "role": OwnerAccount.SUPPORT,
                                                        "is_active": "on", "notify_email": "on"})
        self.assertRedirects(r, reverse("owner:team"), fetch_redirect_response=False)
        sara = OwnerAccount.objects.get(email="sara@owner.test")
        self.assertEqual(sara.role, OwnerAccount.SUPPORT)
        self.assertFalse(sara.check_password(""))
        link = next(l.split(": ", 1)[1].strip() for l in mail.outbox[-1].body.splitlines() if "/reset/" in l)
        client = self.client_class()
        self.assertRedirects(client.post(link, {"password1": "Sara-first-password-1", "password2": "Sara-first-password-1"}), reverse("owner:login"),
                             fetch_redirect_response=False)
        self.assertEqual(sign_in(client, "sara@owner.test", "Sara-first-password-1").status_code, 302)

    def test_duplicate_e_mail_is_refused(self):
        r = self.client.post(reverse("owner:team_add"), {"name": "Dup", "email": "boss@owner.test", "role": OwnerAccount.ADMIN, "is_active": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(OwnerAccount.objects.count(), 1)

    def test_you_cannot_switch_off_or_downgrade_yourself(self):
        self.client.post(reverse("owner:team_edit", args=[self.boss.pk]), {"name": "Boss", "email": "boss@owner.test", "role": OwnerAccount.SUPPORT, "is_active": "on"})
        self.boss.refresh_from_db()
        self.assertTrue(self.boss.is_admin)
        self.client.post(reverse("owner:team_edit", args=[self.boss.pk]), {"name": "Boss", "email": "boss@owner.test", "role": OwnerAccount.ADMIN})
        self.boss.refresh_from_db()
        self.assertTrue(self.boss.is_active)

    def test_reset_link_button_mails_the_member_and_shows_the_link(self):
        sara = make_owner("sara@owner.test", name="Sara")
        r = self.client.post(reverse("owner:team_reset_link", args=[sara.pk]), follow=True)
        self.assertEqual(mail.outbox[-1].to, ["sara@owner.test"])
        self.assertContains(r, "/reset/")


# ------------------------------------------------------------------------------------------------------------------ commands, database
class CommandTests(BillingBase):
    def test_create_owner_creates_and_resets(self):
        out = StringIO()
        call_command("create_owner", "New@Owner.test", "--name", "New Owner", "--password", "a-long-password-1", stdout=out)
        acct = OwnerAccount.objects.get(email="new@owner.test")
        self.assertTrue(acct.is_admin and acct.check_password("a-long-password-1"))
        acct.failed_attempts = 3
        acct.save()
        call_command("create_owner", "new@owner.test", "--password", "another-long-pw-22", stdout=out)
        acct.refresh_from_db()
        self.assertTrue(acct.check_password("another-long-pw-22"))
        self.assertEqual((OwnerAccount.objects.count(), acct.failed_attempts), (1, 0))
        call_command("create_owner", "help@owner.test", "--password", "a-long-password-1", "--support", stdout=out)
        self.assertEqual(OwnerAccount.objects.get(email="help@owner.test").role, OwnerAccount.SUPPORT)

    def test_create_owner_refuses_bad_input(self):
        with self.assertRaises(CommandError):
            call_command("create_owner", "not-an-email", "--password", "a-long-password-1")
        with self.assertRaises(CommandError):
            call_command("create_owner", "a@owner.test", "--password", "short")

    def test_setup_owner_makes_the_account_and_plans(self):
        out = StringIO()
        call_command("setup_owner", "--email", "first@owner.test", "--name", "First", "--password", "a-long-password-1", "--seed-plans", stdout=out)
        self.assertTrue(OwnerAccount.objects.filter(email="first@owner.test").exists())
        self.assertEqual(Plan.objects.count(), 2 + 0)                                   # plans from setUpTestData exist: seeding is skipped
        with self.assertRaises(CommandError):
            call_command("setup_owner", "--email", "first@owner.test", "--password", "a-long-password-1", stdout=out)

    def test_import_legacy_brings_the_old_billing_data_over_once(self):
        from ownerconsole.management.commands.setup_owner import import_legacy
        Plan.objects.all().delete()
        export = {
            "companies": {"7": {"name": "Old Co", "email": "old@example.com", "phone": "0300"}}, "users": {"3": "olduser"},
            "platformsettings": [{"business_name": "Old ERP", "trial_days": 14, "grace_days": 4, "bogus_field": 1}],
            "plan": [{"id": 1, "name": "Old Plan", "slug": "old-plan", "tagline": "", "description": "", "price_monthly": "1000.00", "price_yearly": "10000.00",
                      "currency": "PKR", "modules": ["pos"], "max_users": 1, "max_stores": 1, "max_products": 0, "max_monthly_sales": 0, "features": "",
                      "trial_days": None, "is_active": True, "is_public": True, "is_featured": False, "sort_order": 1}],
            "subscription": [{"id": 1, "company_id": 7, "plan_id": 1, "interval": "monthly", "status": "active", "started_at": "2026-01-01T00:00:00+00:00",
                              "trial_ends_at": None, "current_period_start": "2026-01-01T00:00:00+00:00", "current_period_end": "2026-02-01T00:00:00+00:00",
                              "cancel_at_period_end": False, "cancelled_at": None, "suspended_reason": "", "price_override": None, "max_users_override": None,
                              "max_stores_override": None, "max_products_override": None, "max_monthly_sales_override": None, "notes": "vip"}],
            "invoice": [{"id": 1, "number": "SUB-2026-00001", "company_id": 7, "subscription_id": 1, "plan_id": 1, "plan_name": "Old Plan", "interval": "monthly",
                         "kind": "first", "period_start": "2026-01-01T00:00:00+00:00", "period_end": "2026-02-01T00:00:00+00:00", "subtotal": "1000.00",
                         "tax_amount": "0.00", "total": "1000.00", "currency": "PKR", "status": "paid", "issued_at": "2026-01-01T00:00:00+00:00",
                         "due_at": "2026-01-01T00:00:00+00:00", "paid_at": "2026-01-01T00:00:00+00:00", "note": ""}],
            "payment": [{"id": 1, "invoice_id": 1, "company_id": 7, "amount": "1000.00", "method": "bank", "status": "succeeded", "reference": "R",
                         "proof": "", "note": "", "stripe_session_id": "", "stripe_payment_intent": "", "submitted_by_id": 3, "reviewed_at": None, "review_note": ""}],
            "billingevent": [{"company_id": 7, "kind": "payment", "message": "paid", "actor_id": 3, "created_at": "2026-01-01T00:00:00+00:00"}],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "export.json"
            path.write_text(json.dumps(export))
            counts = import_legacy(path)
            again = import_legacy(path)
        self.assertEqual(counts, {"plans": 1, "subscriptions": 1, "invoices": 1, "payments": 1, "events": 1})
        self.assertEqual((again["subscriptions"], again["invoices"], again["plans"]), (0, 0, 0))
        sub = Subscription.objects.get(company_id=7)
        self.assertEqual((sub.company_name, sub.state(), sub.notes), ("Old Co", "expired", "vip"))
        self.assertEqual(PlatformSettings.get().trial_days, 0)                            # the old "14 free days" default no longer applies
        self.assertEqual(PlatformSettings.get().business_name, "Old ERP")
        self.assertEqual(Payment.objects.get().submitted_by_name, "olduser")

    def test_database_router_keeps_owner_models_on_the_owner_alias(self):
        from ownerconsole import db
        from ownerconsole.router import OwnerRouter
        router = OwnerRouter()
        if db.alias() == "default":                                  # the test-suite keeps both in one database
            self.assertIsNone(router.db_for_read(Plan))
        else:
            self.assertEqual(router.db_for_read(Plan), db.alias())
            self.assertEqual(router.db_for_write(Subscription), db.alias())
        self.assertIsNone(router.db_for_read(User))
        with self.settings(OWNER_DB_ALIAS="owner"):
            self.assertEqual(router.db_for_read(Plan), "owner")
            self.assertEqual(router.db_for_write(Subscription), "owner")
            self.assertIsNone(router.db_for_read(User))
            self.assertTrue(router.allow_migrate("owner", "ownerconsole"))
            self.assertFalse(router.allow_migrate("default", "ownerconsole"))
            self.assertFalse(router.allow_migrate("owner", "accounts"))
            self.assertIsNone(router.allow_migrate("default", "accounts"))


BASE_FORM = {"business_name": "ERP", "support_email": "", "support_phone": "", "invoice_footer": "", "erp_base_url": "", "trial_days": "0", "grace_days": "5",
             "reminder_days": "7,3,1", "renewal_invoice_days": "7", "tax_percent": "0", "allow_public_signup": "on", "manual_enabled": "on",
             "bank_details": "", "jazzcash_number": "", "easypaisa_number": "", "payment_instructions": "", "google_client_id": ""}


@override_settings(**OWNER_SITE)
class DashboardAndSettingsExtrasTests(BillingBase):
    """The richer dashboard and the extra Settings: invoice numbering / due days, announcement, legal links, welcome note, session length, CSV export."""

    def setUp(self):
        self.boss = make_owner()
        self.company, self.user, self.sub = signup("Extra Co", "extra@example.com", self.starter)
        sign_in(self.client)

    def test_dashboard_is_useful_with_no_money_yet_and_after_a_payment(self):
        r = self.client.get(reverse("owner:dashboard"))
        self.assertContains(r, "Needs your attention")
        self.assertContains(r, "No payments yet")                           # empty revenue chart explains itself
        self.assertContains(r, "Company status")
        self.assertEqual(r.context["insights"]["delta"], None)
        pay(self.sub)
        r = self.client.get(reverse("owner:dashboard"))
        self.assertNotContains(r, "No payments yet")
        self.assertEqual(r.context["insights"]["paying"], 1)
        self.assertEqual(r.context["insights"]["best"][0]["name"], "Extra Co")
        self.assertContains(r, "Plans in use")
        self.assertContains(r, "conic-gradient(")

    def test_dashboard_compares_with_last_month_and_lists_what_needs_attention(self):
        pay(self.sub)
        inv = Invoice.objects.get(subscription=self.sub)
        inv.paid_at = timezone.now() - timedelta(days=40)
        inv.save()
        second = signup("Second Co", "second@example.com", self.starter)[2]
        pay(second)
        services.submit_manual_payment(services.open_invoice(second), user_name="x", method=Payment.BANK, reference="W1")
        r = self.client.get(reverse("owner:dashboard"))
        ins = r.context["insights"]
        self.assertGreater(ins["this_month"], 0)
        self.assertEqual(ins["last_month"] > 0, ins["delta"] is not None or True)
        self.assertTrue(any("waiting for your approval" in a["text"] for a in r.context["attention"]))
        self.assertEqual(sum(w["count"] for w in ins["weeks"]), 2)

    def test_invoice_prefix_and_due_days_apply_to_new_invoices(self):
        data = dict(BASE_FORM, invoice_prefix="inv", payment_due_days="5")
        self.assertRedirects(self.client.post(reverse("owner:settings"), data), reverse("owner:settings"))
        s = PlatformSettings.get()
        self.assertEqual((s.invoice_prefix, s.payment_due_days), ("INV", 5))
        _, _, other = signup("Prefix Co", "prefix@example.com", self.starter)
        inv = services.open_invoice(other)
        self.assertTrue(inv.number.startswith(f"INV-{timezone.now().year}-"), inv.number)
        self.assertAlmostEqual((inv.due_at - timezone.now()).total_seconds(), 5 * 86400, delta=120)
        self.assertEqual(self.client.post(reverse("owner:settings"), dict(data, invoice_prefix="bad prefix!")).status_code, 200)

    def test_blank_new_fields_fall_back_to_defaults(self):
        self.assertRedirects(self.client.post(reverse("owner:settings"), BASE_FORM), reverse("owner:settings"))
        s = PlatformSettings.get()
        self.assertEqual((s.invoice_prefix, s.payment_due_days, s.session_hours), ("SUB", 0, 12))

    def test_announcement_needs_text_and_is_saved(self):
        data = dict(BASE_FORM, announcement_enabled="on", announcement_text="", announcement_level="warning")
        self.assertEqual(self.client.post(reverse("owner:settings"), data).status_code, 200)
        self.assertFalse(PlatformSettings.get().announcement_enabled)
        self.assertRedirects(self.client.post(reverse("owner:settings"), dict(data, announcement_text="Maintenance tonight 11pm")), reverse("owner:settings"))
        s = PlatformSettings.get()
        self.assertEqual((s.announcement_enabled, s.announcement_text, s.announcement_level), (True, "Maintenance tonight 11pm", "warning"))
        self.assertContains(self.client.get(reverse("owner:settings")), "LIVE")

    def test_legal_links_and_welcome_note_are_saved_and_the_note_is_emailed(self):
        self.client.post(reverse("owner:settings"), dict(BASE_FORM, terms_url="https://example.com/terms", privacy_url="https://example.com/privacy",
                                                         welcome_note="WhatsApp us on 0300-1234567"))
        s = PlatformSettings.get()
        self.assertEqual((s.terms_url, s.privacy_url), ("https://example.com/terms", "https://example.com/privacy"))
        self.assertEqual(self.client.post(reverse("owner:settings"), dict(BASE_FORM, terms_url="not a url")).status_code, 200)
        mail.outbox.clear()
        signup("Welcome Co", "welcome@example.com", self.starter)
        self.assertTrue(any("WhatsApp us on 0300-1234567" in m.body for m in mail.outbox if "welcome@example.com" in m.to))

    def test_session_hours_decide_how_long_a_sign_in_lasts(self):
        self.client.post(reverse("owner:settings"), dict(BASE_FORM, session_hours="2"))
        self.assertEqual(PlatformSettings.get().session_hours, 2)
        self.assertEqual(self.client.post(reverse("owner:settings"), dict(BASE_FORM, session_hours="100")).status_code, 200)
        from django.core import signing
        from django.http import HttpResponse
        from django.test import RequestFactory
        response = auth.sign_in(HttpResponse(), self.boss, RequestFactory().get("/"))
        payload = signing.loads(response.cookies[auth.cookie_name()].value, salt=auth.SESSION_SALT)
        self.assertAlmostEqual(payload["x"] - __import__("time").time(), 2 * 3600, delta=60)

    def test_csv_exports_are_admin_only_safe_and_complete(self):
        evil = signup("=HYPERLINK(\"http://x\")", "evil@example.com", self.starter)[2]
        pay(evil)
        for kind, needle in (("companies", "Extra Co"), ("invoices", "SUB-"), ("payments", "Invoice")):
            r = self.client.get(reverse("owner:export", args=[kind]))
            self.assertEqual(r.status_code, 200)
            self.assertIn("text/csv", r["Content-Type"])
            self.assertIn(needle, r.content.decode("utf-8"))
            self.assertIn(f"{kind}-", r["Content-Disposition"])
        body = self.client.get(reverse("owner:export", args=["companies"])).content.decode("utf-8")
        self.assertIn("'=HYPERLINK", body)                                          # never starts with = in a spreadsheet cell
        self.assertNotIn(",=HYPERLINK", body)
        self.assertEqual(self.client.get("/export/other.csv").status_code, 404)
        make_owner("help2@owner.test", role=OwnerAccount.SUPPORT, name="Help")
        support = self.client.__class__()
        sign_in(support, "help2@owner.test")
        self.assertRedirects(support.get(reverse("owner:export", args=["companies"])), reverse("owner:dashboard"))      # support role: not allowed
        self.assertEqual(self.client.__class__().get(reverse("owner:export", args=["companies"])).status_code, 302)   # signed out -> sign-in page

    def test_settings_page_lists_the_new_sections(self):
        r = self.client.get(reverse("owner:settings"))
        for text in ("Announcement banner", "Invoices", "Security", "Data export", "Sign-up page and welcome e-mail"):
            self.assertContains(r, text)


class SecondSiteTests(TestCase):
    def test_the_owner_site_passes_django_system_checks_on_its_own_settings(self):
        """The console is started with --settings=config.owner_settings; that must boot (no admin, no ERP middleware needed)."""
        import subprocess
        import sys
        from django.conf import settings
        result = subprocess.run([sys.executable, "manage.py", "check", "--settings=config.owner_settings"], cwd=settings.BASE_DIR,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_owner_site_urls_contain_no_erp_views(self):
        from django.urls import URLPattern, get_resolver

        def callbacks(patterns):
            for p in patterns:
                if isinstance(p, URLPattern):
                    yield p.callback
                else:
                    yield from callbacks(p.url_patterns)
        modules = {cb.__module__ for cb in callbacks(get_resolver("config.owner_urls").url_patterns)}
        self.assertEqual({m.split(".")[0] for m in modules}, {"ownerconsole"}, modules)
