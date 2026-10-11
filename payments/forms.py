import re

from django import forms

from .models import PaymentMethod


class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["name", "code", "requires_reference", "sort_order", "is_active"]
        labels = {"name": "Display name", "code": "Code", "requires_reference": "Ask for a reference / transaction ID",
                  "sort_order": "Order in the payment window", "is_active": "Enabled at the POS"}

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.fields["code"].help_text = "Short id stored on every payment, e.g. nayapay. Letters, numbers, _ and - only. Cannot be changed later."
        self.fields["code"].widget.attrs.update({"autocomplete": "off"})
        if self.instance.pk:                          # the code is history - never rewrite it
            self.fields["code"].disabled = True

    def clean_code(self):
        if self.instance.pk:
            return self.instance.code
        code = (self.cleaned_data.get("code") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{2,15}", code):
            raise forms.ValidationError("Use 2-15 letters, numbers, _ or - (no spaces).")
        if PaymentMethod.objects.filter(store=self.store, code=code).exists():
            raise forms.ValidationError("This branch already has a payment method with this code.")
        return code

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if PaymentMethod.objects.filter(store=self.store, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another payment method already has this name.")
        return name

    def clean_is_active(self):
        active = self.cleaned_data.get("is_active")
        if not active and self.instance.pk and self.instance.is_active:
            others = PaymentMethod.objects.filter(store=self.store, is_active=True).exclude(pk=self.instance.pk)
            if not others.exists():
                raise forms.ValidationError("At least one payment method must stay enabled.")
        return active
