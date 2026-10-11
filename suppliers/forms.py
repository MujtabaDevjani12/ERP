from django import forms
from django.forms import inlineformset_factory

from django.db.models import Q

from .models import PurchaseOrder, PurchaseOrderItem, PurchasePayment, Supplier


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "company", "contact_person", "phone", "email", "tax_no", "payment_term_days",
                  "address", "city", "state", "postal_code", "country", "notes", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Ali Traders", "autocomplete": "off"}),
            "company": forms.TextInput(attrs={"placeholder": "Registered / trade name (if different)"}),
            "contact_person": forms.TextInput(attrs={"placeholder": "Who do we call?"}),
            "phone": forms.TextInput(attrs={"placeholder": "03xx-xxxxxxx", "inputmode": "tel"}),
            "email": forms.EmailInput(attrs={"placeholder": "accounts@supplier.com"}),
            "tax_no": forms.TextInput(attrs={"placeholder": "NTN / STRN"}),
            "payment_term_days": forms.NumberInput(attrs={"min": 0, "max": 365, "step": 1}),
            "address": forms.TextInput(attrs={"placeholder": "Street / area"}),
            "notes": forms.Textarea(attrs={"rows": 3, "placeholder": "Delivery days, bank details, anything worth remembering"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_term_days"].required = False          # left empty = pay on delivery (0)

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()

    def clean_payment_term_days(self):
        days = self.cleaned_data.get("payment_term_days")
        return 0 if days is None else days


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ["supplier", "warehouse", "order_date", "expected_date", "status", "supplier_ref", "note"]
        widgets = {
            "order_date": forms.DateInput(attrs={"type": "date"}),
            "expected_date": forms.DateInput(attrs={"type": "date"}),
            "supplier_ref": forms.TextInput(attrs={"placeholder": "The supplier's own invoice / bill number"}),
            "status": forms.Select(attrs={"x-model": "status"}),
            "note": forms.Textarea(attrs={"rows": 2, "placeholder": "Optional - delivery instructions, agreed terms..."}),
        }

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        if store is not None:
            keep = Q(is_active=True) | Q(pk=getattr(self.instance, "supplier_id", None))     # inactive suppliers stay on old orders
            self.fields["supplier"].queryset = Supplier.objects.filter(keep, store=store).order_by("name")
            self.fields["warehouse"].queryset = store.warehouses.all()
        self.fields["supplier"].empty_label = "Choose a supplier"
        self.fields["warehouse"].empty_label = "Receiving warehouse"

    def clean(self):
        data = super().clean()
        order, expected = data.get("order_date"), data.get("expected_date")
        if order and expected and expected < order:
            self.add_error("expected_date", "The expected delivery cannot be before the order date.")
        return data


class PurchaseOrderItemForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrderItem
        fields = ["variant", "quantity", "cost_price"]

    def clean_quantity(self):
        q = self.cleaned_data.get("quantity")
        if q is not None and q <= 0:
            raise forms.ValidationError("Quantity must be more than zero.")
        return q

    def clean_cost_price(self):
        c = self.cleaned_data.get("cost_price")
        if c is not None and c < 0:
            raise forms.ValidationError("The cost cannot be negative.")
        return c


class _RequireLines(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        kept = [f for f in self.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
        if not kept:
            raise forms.ValidationError("Add at least one product to the order.")
        seen = set()
        for f in kept:
            v = f.cleaned_data["variant"]
            if v.pk in seen:
                raise forms.ValidationError(f"{v} is on the order twice - merge the lines into one.")
            seen.add(v.pk)


PurchaseOrderItemFormSet = inlineformset_factory(
    PurchaseOrder, PurchaseOrderItem, form=PurchaseOrderItemForm, formset=_RequireLines,
    fields=["variant", "quantity", "cost_price"], extra=0, can_delete=True,
)


class PurchasePaymentForm(forms.ModelForm):
    class Meta:
        model = PurchasePayment
        fields = ["amount", "method", "paid_on", "note"]
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "paid_on": forms.DateInput(attrs={"type": "date"}),
            "note": forms.TextInput(attrs={"placeholder": "Optional"}),
        }
