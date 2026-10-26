"""E-mail one-time codes: company sign-up (form and Google) and forgot-password."""
from unittest import mock

from django.core import mail
from django.urls import reverse

from accounts import otp
from billing.testkit import BillingBase, last_code, signup
from company.models import Company
from core.pos_fixtures import PASSWORD

FORM = {"company_name": "Otp Co", "owner_name": "Otto P", "email": "otto@example.com", "phone": "0300", "password1": PASSWORD, "password2": PASSWORD,
        "interval": "monthly", "accept": "on"}


class SignupOtpTests(BillingBase):
    def post_form(self, **over):
        return self.client.post(reverse("billing:signup"), dict(FORM, plan=self.starter.pk, **over))

    def test_the_code_is_e_mailed_to_the_address_typed(self):
        self.post_form()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["otto@example.com"])
        self.assertIn("verification code", mail.outbox[0].subject)

    def test_wrong_code_creates_nothing_and_right_code_creates_the_company(self):
        self.post_form()
        bad = self.client.post(reverse("billing:signup_verify"), {"code": "000000" if last_code() != "000000" else "111111"})
        self.assertContains(bad, "not correct")
        self.assertEqual(Company.objects.count(), 0)
        ok = self.client.post(reverse("billing:signup_verify"), {"code": last_code()})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(Company.objects.filter(name="Otp Co").count(), 1)
        from accounts.models import User
        self.assertTrue(User.objects.get(email="otto@example.com").check_password(PASSWORD))   # the password typed at sign-up works

    def test_verify_page_needs_a_started_signup(self):
        self.assertRedirects(self.client.get(reverse("billing:signup_verify")), reverse("billing:signup"))

    def test_a_code_from_another_flow_is_not_accepted(self):
        self.post_form()
        code = last_code()
        other = self.client_class()
        self.assertRedirects(other.post(reverse("billing:signup_verify"), {"code": code}), reverse("billing:signup"))   # other browser: nothing started
        self.assertEqual(Company.objects.count(), 0)

    def test_mail_failure_is_reported_and_nothing_is_started(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", side_effect=OSError("smtp down")):
            r = self.post_form()
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "could not send the email")
        self.assertRedirects(self.client.get(reverse("billing:signup_verify")), reverse("billing:signup"))

    def test_resend_sends_a_second_code_after_the_cool_down(self):
        self.post_form()
        self.assertContains(self.client.post(reverse("billing:signup_verify"), {"action": "resend"}), "Please wait")
        s = self.client.session
        s["otp:signup"]["last"] -= 100
        s.save()
        self.client.post(reverse("billing:signup_verify"), {"action": "resend"})
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(self.client.post(reverse("billing:signup_verify"), {"code": last_code()}).status_code, 302)

    def test_per_address_hourly_limit(self):
        from django.core.cache import cache
        cache.clear()
        for _ in range(otp.PER_EMAIL_PER_HOUR):
            self.post_form()
        r = self.post_form()
        self.assertContains(r, "Too many codes")
        cache.clear()

    def test_the_code_itself_is_not_kept_in_the_session(self):
        self.post_form()
        code = last_code()
        self.assertNotIn(code, str(dict(self.client.session)))

    def test_existing_company_cannot_be_hijacked_by_a_second_signup_with_the_same_email(self):
        signup("First", "otto@example.com", self.starter)
        before = len(mail.outbox)
        r = self.post_form()
        self.assertContains(r, "Sign in instead")
        self.assertEqual(len(mail.outbox), before)


class MaskTests(BillingBase):
    def test_mask(self):
        self.assertEqual(otp.mask("sam463782@gmail.com"), "s********@gmail.com")
        self.assertEqual(otp.mask("ab@x.com"), "a**@x.com")
