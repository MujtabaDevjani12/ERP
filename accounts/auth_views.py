"""Sign-in and forgot-password screens. The pages themselves live in templates/registration/ (two-panel "lx" layout)."""
import logging

from django.contrib.auth import views as auth_views
from django.core.cache import cache
from django.urls import reverse_lazy

logger = logging.getLogger(__name__)

RESET_LIMIT = 5                 # reset emails per visitor (IP) per hour — stops the form being used to spam someone's inbox
RESET_WINDOW = 60 * 60


class SignInView(auth_views.LoginView):
    template_name = "registration/login.html"

    def form_valid(self, form):
        response = super().form_valid(form)
        if not self.request.POST.get("remember"):
            self.request.session.set_expiry(0)               # session ends when the browser closes
        return response


class ResetRequestView(auth_views.PasswordResetView):
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.txt"
    html_email_template_name = "registration/password_reset_email.html"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def _throttled(self):
        ip = (self.request.META.get("REMOTE_ADDR") or "unknown")
        key = f"pwreset:{ip}"
        count = cache.get(key, 0)
        if count >= RESET_LIMIT:
            return True
        cache.set(key, count + 1, RESET_WINDOW)
        return False

    def form_valid(self, form):
        from configuration.mailing import sender_identity
        from configuration.models import SystemConfiguration

        if self._throttled():
            form.add_error(None, "Too many requests from this device. Please try again in an hour.")
            return self.form_invalid(form)
        self.from_email = sender_identity()[0]                              # Settings > Email delivery
        self.extra_email_context = {"site_name": SystemConfiguration.get().site_name}
        try:
            return super().form_valid(form)
        except Exception:                                                   # noqa: BLE001 — mail server down / not configured
            logger.exception("Password reset email could not be sent")
            form.add_error(None, "We could not send the email right now. Please try again later, or ask an administrator to reset your password.")
            return self.form_invalid(form)


class ResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "registration/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class ResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "registration/password_reset_done.html"


class ResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "registration/password_reset_complete.html"
