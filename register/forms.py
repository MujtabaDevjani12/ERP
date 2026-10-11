from django import forms
from django.db.models import Q

from accounts.models import User
from tenants.models import Warehouse
from .models import Register


class CounterForm(forms.ModelForm):
    """Create / edit a counter (physical POS terminal)."""

    class Meta:
        model = Register
        fields = ["name", "code", "warehouse", "default_payment_method", "assigned_users", "is_active"]
        labels = {
            "name": "Counter name",
            "warehouse": "Assigned warehouse",
            "default_payment_method": "Default payment method",
            "assigned_users": "Assigned users",
            "is_active": "Counter is active",
        }
        widgets = {"assigned_users": forms.CheckboxSelectMultiple}

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        from payments.models import PaymentMethod
        self.store = store
        self.fields["name"].widget.attrs.update({"placeholder": "Counter 01", "autocomplete": "off"})
        self.fields["code"].widget.attrs.update({"placeholder": "C01", "autocomplete": "off"})
        # Sales-floor warehouses first. Leaving it blank uses the branch's default warehouse (created on the spot if the
        # branch has none yet), so a counter can never be saved without one.
        self.fields["warehouse"].queryset = Warehouse.objects.filter(store=store).order_by("-is_default", "name")
        self.fields["warehouse"].empty_label = "Branch default warehouse"
        self.fields["warehouse"].required = False
        self.fields["warehouse"].help_text = "Stock for this counter's sales is taken from here. Leave on default to use the branch's main warehouse."
        if not self.instance.pk and not self.initial.get("warehouse"):
            default = self.default_warehouse(store, create=False)
            if default:
                self.initial["warehouse"] = default.pk
        self.fields["assigned_users"].queryset = User.objects.filter(is_active=True).filter(
            Q(store=store) | Q(store__isnull=True)).order_by("username")
        self.fields["assigned_users"].required = False
        self.fields["default_payment_method"] = forms.ChoiceField(
            required=False, label="Default payment method",
            choices=[("", "— none —")] + [(m.code, m.name) for m in PaymentMethod.active_for(store)],
            help_text="Preselected in the payment window at this counter.")

    @staticmethod
    def default_warehouse(store, create=True):
        qs = Warehouse.objects.filter(store=store)
        sales_types = [Warehouse.GENERAL, Warehouse.OUTLET, Warehouse.FINISHED_GOODS]
        found = (qs.filter(is_default=True, warehouse_type__in=sales_types).first()
                 or qs.filter(warehouse_type__in=sales_types).order_by("-is_default", "name").first()
                 or qs.filter(is_default=True).first() or qs.order_by("name").first())
        if found is None and create:
            found = Warehouse.objects.create(store=store, name="Main Warehouse", is_default=True)
        return found

    def clean_warehouse(self):
        warehouse = self.cleaned_data.get("warehouse")
        if warehouse is None:
            warehouse = self.default_warehouse(self.store)
        return warehouse

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Register.objects.filter(store=self.store, name__iexact=name).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Another counter in this branch already has this name.")
        return name

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        if code and Register.objects.filter(store=self.store, code=code).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Another counter in this branch already uses this code.")
        return code

    def clean_is_active(self):
        active = self.cleaned_data.get("is_active")
        if not active and self.instance.pk and self.instance.sessions.filter(status="open").exists():
            raise forms.ValidationError("This counter has an open register session. Close it before switching the counter off.")
        return active
