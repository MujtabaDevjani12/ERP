from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from ownerconsole.models import Payment, Plan, Subscription


class SignupForm(forms.Form):
    company_name = forms.CharField(max_length=150, label="Company name")
    owner_name = forms.CharField(max_length=150, label="Your name")
    email = forms.EmailField(label="Work email")
    phone = forms.CharField(max_length=30, required=False, label="Phone")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")
    plan = forms.ModelChoiceField(queryset=Plan.objects.none(), empty_label=None, widget=forms.RadioSelect)
    interval = forms.ChoiceField(choices=Subscription.INTERVALS, initial=Subscription.MONTHLY, widget=forms.RadioSelect)
    accept = forms.BooleanField(label="I agree to the terms of service")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = Plan.objects.filter(is_active=True, is_public=True)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists. Sign in instead.")
        return email

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "The two passwords do not match.")
        elif p1:
            try:
                validate_password(p1)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data


class ManualPaymentForm(forms.Form):
    method = forms.ChoiceField(choices=[m for m in Payment.METHODS if m[0] != Payment.STRIPE], label="How did you pay?")
    reference = forms.CharField(max_length=120, required=False, label="Transaction / receipt number")
    proof = forms.FileField(required=False, label="Receipt or screenshot")
    note = forms.CharField(max_length=255, required=False, label="Note (optional)")

    def clean_proof(self):
        f = self.cleaned_data.get("proof")
        if f:
            if f.size > 5 * 1024 * 1024:
                raise ValidationError("The file is larger than 5 MB.")
            if not (f.name.lower().endswith((".jpg", ".jpeg", ".png", ".pdf", ".webp"))):
                raise ValidationError("Upload a JPG, PNG, WebP or PDF.")
        return f

    def clean(self):
        data = super().clean()
        if not data.get("reference", "").strip() and not data.get("proof") and not self.errors:
            raise ValidationError("Enter the transaction number or attach the receipt so we can find your payment.")
        return data
