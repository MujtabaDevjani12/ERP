from django import forms

from .models import Customer, GiftCard



class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["group", "name", "phone", "email", "cnic", "address", "is_active"]
        widgets = {
            "group": forms.Select(attrs={}),
            "name": forms.TextInput(attrs={}),
            "phone": forms.TextInput(attrs={"placeholder": "03xx-xxxxxxx"}),
            "email": forms.EmailInput(attrs={}),
            "cnic": forms.TextInput(attrs={"placeholder": "35201-1234567-1"}),
            "address": forms.TextInput(attrs={}),
        }

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        if store is not None:
            self.fields["group"].queryset = store.customer_groups.all()


class GiftCardForm(forms.ModelForm):
    """A new card starts with its full amount; after that the balance only changes through top-ups, POS spending and refunds
    (so the history always explains it)."""
    class Meta:
        model = GiftCard
        fields = ["code", "customer", "initial_balance", "expires_on", "is_active"]
        widgets = {
            "code": forms.TextInput(attrs={}),
            "customer": forms.Select(attrs={}),
            "initial_balance": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "expires_on": forms.DateInput(attrs={"type": "date"}),
        }
        labels = {"initial_balance": "Card value", "code": "Card number"}

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        if store is not None:
            self.fields["customer"].queryset = Customer.objects.filter(store=store)
        if self.instance.pk:
            self.fields.pop("initial_balance")            # the value it was sold for is history, not editable

    def clean_code(self):
        code = self.cleaned_data["code"].strip().upper()
        if code.startswith("DEP-") and not (self.instance.pk and self.instance.code == code):
            raise forms.ValidationError("Card numbers starting with DEP- are reserved for customer deposits.")
        return code

    def clean_initial_balance(self):
        value = self.cleaned_data["initial_balance"]
        if value is None or value <= 0:
            raise forms.ValidationError("The card value must be more than zero.")
        return value
