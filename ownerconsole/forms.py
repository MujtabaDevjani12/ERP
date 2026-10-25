from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from billing.modules import module_choices
from .models import OwnerAccount, PlatformSettings, Plan, Subscription


# ---------------------------------------------------------------- sign in / reset / profile
class LoginForm(forms.Form):
    email = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True, "placeholder": "you@company.com"}))
    password = forms.CharField(label="Password", widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "Your password"}))
    remember = forms.BooleanField(required=False, label="Keep me signed in on this device")


class ForgotForm(forms.Form):
    email = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True, "placeholder": "you@company.com"}))


class SetPasswordForm(forms.Form):
    password1 = forms.CharField(label="New password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(label="Repeat new password", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        elif p1:
            if len(p1) < 10:
                self.add_error("password1", "Use at least 10 characters for the owner console.")
            else:
                try:
                    validate_password(p1)
                except ValidationError as exc:
                    self.add_error("password1", exc)
        if p1 and self.owner is not None and self.owner.check_password(p1):
            self.add_error("password1", "That is your current password. Choose a new one.")
        return data


class ChangePasswordForm(SetPasswordForm):
    current = forms.CharField(label="Current password", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    field_order = ["current", "password1", "password2"]

    def clean_current(self):
        if not self.owner.check_password(self.cleaned_data["current"]):
            raise ValidationError("Your current password is not correct.")
        return self.cleaned_data["current"]


class ProfileForm(forms.ModelForm):
    class Meta:
        model = OwnerAccount
        fields = ["name", "email", "phone", "notify_email"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if OwnerAccount.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Another owner account already uses this e-mail.")
        return email


class TeamMemberForm(forms.ModelForm):
    class Meta:
        model = OwnerAccount
        fields = ["name", "email", "phone", "role", "is_active", "notify_email"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if OwnerAccount.objects.filter(email=email).exclude(pk=self.instance.pk).exists():
            raise ValidationError("Another owner account already uses this e-mail.")
        return email


# ---------------------------------------------------------------- plans
class PlanForm(forms.ModelForm):
    modules = forms.MultipleChoiceField(choices=module_choices(), widget=forms.CheckboxSelectMultiple, required=False,
                                        help_text="Everything not ticked stays hidden and locked for this plan.")

    class Meta:
        model = Plan
        fields = ["name", "slug", "tagline", "description", "price_monthly", "price_yearly", "currency", "modules",
                  "max_users", "max_stores", "max_products", "max_monthly_sales", "features", "trial_days",
                  "is_active", "is_public", "is_featured", "sort_order"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3}), "features": forms.Textarea(attrs={"rows": 5, "placeholder": "One benefit per line"})}

    def clean(self):
        data = super().clean()
        for f in ("price_monthly", "price_yearly"):
            if data.get(f) is not None and data[f] < 0:
                self.add_error(f, "Price cannot be negative.")
        return data

    def clean_modules(self):
        return list(self.cleaned_data.get("modules") or [])


# ---------------------------------------------------------------- settings
def _secret(label, kept):
    return forms.CharField(required=False, label=label, widget=forms.PasswordInput(
        render_value=False, attrs={"autocomplete": "new-password", "placeholder": f"Leave blank to keep the saved {kept}"}))


class PlatformSettingsForm(forms.ModelForm):
    """Business, billing rules, payment methods and Google sign-in. Secrets are never echoed back; blank = keep the saved value."""
    stripe_secret_key = _secret("Stripe secret key", "key")
    stripe_webhook_secret = _secret("Stripe webhook secret", "secret")
    google_client_secret = _secret("Google client secret", "secret")

    class Meta:
        model = PlatformSettings
        fields = ["business_name", "support_email", "support_phone", "invoice_footer", "erp_base_url", "trial_days", "grace_days", "reminder_days",
                  "renewal_invoice_days", "tax_percent", "allow_public_signup", "stripe_enabled", "stripe_publishable_key",
                  "stripe_secret_key", "stripe_webhook_secret", "manual_enabled", "bank_details", "jazzcash_number",
                  "easypaisa_number", "payment_instructions", "google_client_id", "google_client_secret",
                  "invoice_prefix", "payment_due_days", "welcome_note", "terms_url", "privacy_url",
                  "announcement_enabled", "announcement_text", "announcement_level", "session_hours"]
        widgets = {"bank_details": forms.Textarea(attrs={"rows": 3}), "payment_instructions": forms.Textarea(attrs={"rows": 3}),
                   "welcome_note": forms.Textarea(attrs={"rows": 2}), "announcement_text": forms.TextInput(attrs={"placeholder": "Planned maintenance tonight 11pm - 12am"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("invoice_prefix", "payment_due_days", "session_hours", "announcement_level"):      # blank = keep the sensible default
            self.fields[name].required = False
        for name, label in (("erp_base_url", "ERP address (URL)"), ("terms_url", "Terms of service link"), ("privacy_url", "Privacy policy link"),
                            ("jazzcash_number", "JazzCash number"), ("easypaisa_number", "Easypaisa number"), ("google_client_id", "Google client ID"),
                            ("announcement_enabled", "Show the announcement"), ("announcement_text", "Message"), ("announcement_level", "Style"),
                            ("session_hours", "Sign-in lasts (hours)"), ("invoice_prefix", "Invoice prefix"), ("payment_due_days", "Days to pay a new invoice")):
            self.fields[name].label = label

    def clean_announcement_level(self):
        return self.cleaned_data.get("announcement_level") or "info"

    def clean_payment_due_days(self):
        days = self.cleaned_data.get("payment_due_days")
        return 0 if days is None else days

    def clean_invoice_prefix(self):
        value = self.cleaned_data.get("invoice_prefix", "").strip().upper()
        if not value:
            return "SUB"
        if not value.replace("_", "").isalnum():
            raise ValidationError("Use letters and numbers only, for example INV or ABC.")
        return value

    def clean_session_hours(self):
        hours = self.cleaned_data.get("session_hours") or 12
        if not 1 <= hours <= 72:
            raise ValidationError("Choose between 1 and 72 hours.")
        return hours

    def clean_reminder_days(self):
        value = self.cleaned_data["reminder_days"]
        parts = [p.strip() for p in value.split(",") if p.strip()]
        if not parts or not all(p.isdigit() and 0 < int(p) <= 90 for p in parts):
            raise ValidationError("Use whole numbers between 1 and 90, separated by commas, for example 7,3,1.")
        return ",".join(parts)

    def clean_erp_base_url(self):
        url = self.cleaned_data.get("erp_base_url", "").strip().rstrip("/")
        if url and not url.lower().startswith(("http://", "https://")):
            raise ValidationError("Start with http:// or https://")
        return url

    def clean(self):
        data = super().clean()
        for key in ("stripe_secret_key", "stripe_webhook_secret", "google_client_secret"):
            if not data.get(key):
                data[key] = getattr(self.instance, key)
        if data.get("announcement_enabled") and not (data.get("announcement_text") or "").strip():
            self.add_error("announcement_text", "Write the message to show, or switch the announcement off.")
        if data.get("stripe_enabled") and not data.get("stripe_secret_key"):
            self.add_error("stripe_secret_key", "Enter the Stripe secret key to switch Stripe on.")
        if not data.get("stripe_enabled") and not data.get("manual_enabled"):
            raise ValidationError("Switch on at least one way to pay (Stripe or manual).")
        return data


class EmailSettingsForm(forms.ModelForm):
    email_password = _secret("SMTP password", "password")

    class Meta:
        model = PlatformSettings
        fields = ["email_host", "email_port", "email_username", "email_password", "email_use_tls", "email_use_ssl",
                  "email_from_name", "email_from_address", "email_reply_to", "send_company_emails", "email_owners"]

    def clean(self):
        data = super().clean()
        if not data.get("email_password"):
            data["email_password"] = self.instance.email_password
        if data.get("email_use_tls") and data.get("email_use_ssl"):
            raise ValidationError("Choose either STARTTLS or SSL, not both.")
        if data.get("email_host") and not (data.get("email_from_address") or data.get("email_username")):
            self.add_error("email_from_address", "Enter the address the messages come from (or the SMTP username).")
        return data


class TestEmailForm(forms.Form):
    to = forms.EmailField(label="Send a test message to")


# ---------------------------------------------------------------- company control
class SubscriptionControlForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=Plan.objects.all())
    interval = forms.ChoiceField(choices=Subscription.INTERVALS)
    price_override = forms.DecimalField(required=False, min_value=0, max_digits=12, decimal_places=2, label="Special price per period")
    max_users_override = forms.IntegerField(required=False, min_value=0, label="Users limit")
    max_stores_override = forms.IntegerField(required=False, min_value=0, label="Branches limit")
    max_products_override = forms.IntegerField(required=False, min_value=0, label="Products limit")
    max_monthly_sales_override = forms.IntegerField(required=False, min_value=0, label="Sales per month limit")
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Private notes")
