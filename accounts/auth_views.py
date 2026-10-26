"""Sign-in and forgot-password screens. The pages themselves live in templates/registration/ (two-panel "lx" layout)."""
import logging
import time

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import SetPasswordForm
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_http_methods

from . import otp
from .otp_views import verify_page

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
    success_url = reverse_lazy("accounts:password_reset_verify")

    def _throttled(self):
        ip = (self.request.META.get("REMOTE_ADDR") or "unknown")
        key = f"pwreset:{ip}"
        count = cache.get(key, 0)
        if count >= RESET_LIMIT:
            return True
        cache.set(key, count + 1, RESET_WINDOW)
        return False

    def form_valid(self, form):
        if self._throttled():
            form.add_error(None, "Too many requests from this device. Please try again in an hour.")
            return self.form_invalid(form)
        email = form.cleaned_data["email"].strip().lower()
        users = list(form.get_users(email))
        try:
            # an unknown address gets exactly the same screens (nothing is sent) so the form cannot be used to find out who has an account
            otp.start(self.request, "reset", email, {"uid": users[0].pk} if users else {}, real=bool(users))
        except otp.OtpError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        return redirect("accounts:password_reset_verify")


RESET_OK = "reset_ok"
RESET_OK_SECONDS = 15 * 60


def _reset_verified(request, state):
    uid = (state.get("payload") or {}).get("uid")
    if not uid:
        messages.error(request, "That code could not be used. Please start again.")
        return redirect("accounts:password_reset")
    request.session[RESET_OK] = {"uid": uid, "exp": time.time() + RESET_OK_SECONDS}
    return redirect("accounts:password_reset_new")


@require_http_methods(["GET", "POST"])
def reset_verify(request):
    """Step 2 of forgot-password: type the 6-digit code that was e-mailed."""
    return verify_page(request, "reset", title="Enter your code", lead="To reset your password,", back_url=reverse("accounts:password_reset"),
                       back_label="Use a different email", on_success=_reset_verified)


@require_http_methods(["GET", "POST"])
def reset_new_password(request):
    """Step 3: choose the new password (only reachable right after a correct code)."""
    ok = request.session.get(RESET_OK)
    user = get_user_model().objects.filter(pk=ok["uid"], is_active=True).first() if ok and ok["exp"] > time.time() else None
    if user is None:
        request.session.pop(RESET_OK, None)
        messages.error(request, "Please confirm your email with a code first.")
        return redirect("accounts:password_reset")
    form = SetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        request.session.pop(RESET_OK, None)
        return redirect("accounts:password_reset_complete")
    return render(request, "registration/password_reset_confirm.html", {"form": form, "validlink": True})


class ResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "registration/password_reset_confirm.html"
    success_url = reverse_lazy("accounts:password_reset_complete")


class ResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "registration/password_reset_done.html"


class ResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "registration/password_reset_complete.html"
