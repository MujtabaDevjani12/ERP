"""Warehouses of the current branch: list, add / edit in the side drawer, set the default. No delete on purpose - stock, counters and the
ERP ledger all point at a warehouse, so removing one would orphan history."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from tenants.models import Warehouse


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ["name", "code", "warehouse_type", "is_default"]
        labels = {"name": "Warehouse name", "warehouse_type": "Type", "is_default": "Use as the branch's default warehouse"}

    def __init__(self, *args, store, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        self.fields["name"].widget.attrs.update({"placeholder": "Main store", "autocomplete": "off"})
        self.fields["code"].widget.attrs.update({"placeholder": "WH-01", "autocomplete": "off"})
        self.fields["code"].help_text = "Optional short code shown on reports."

    def clean_name(self):
        name = (self.cleaned_data["name"] or "").strip()
        if Warehouse.objects.filter(store=self.store, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This branch already has a warehouse with that name.")
        return name

    def clean_is_default(self):
        value = self.cleaned_data["is_default"]
        if self.instance.pk and self.instance.is_default and not value:
            raise forms.ValidationError("Make another warehouse the default instead - a branch always needs one.")
        return value


def _qs(request):
    return Warehouse.objects.filter(store=request.store)


@login_required
@permission_required("tenants.view_warehouse", raise_exception=True)
def warehouse_list(request):
    rows = list(_qs(request).annotate(n_products=Count("stock_items", filter=Q(stock_items__quantity__gt=0), distinct=True),
                                     units=Sum("stock_items__quantity"), n_counters=Count("registers", distinct=True)).order_by("-is_default", "name"))
    return render(request, "inventory/warehouse_list.html", {"warehouses": rows})


@login_required
def warehouse_form(request, pk=None):
    if not request.user.has_perm("tenants.change_warehouse" if pk else "tenants.add_warehouse"):
        raise PermissionDenied
    instance = get_object_or_404(_qs(request), pk=pk) if pk else None
    if request.method == "GET" and request.headers.get("x-requested-with") != "XMLHttpRequest":
        return redirect("inventory:warehouse_list")
    if request.method == "POST":
        form = WarehouseForm(request.POST, instance=instance, store=request.store)
        if form.is_valid():
            with transaction.atomic():
                wh = form.save(commit=False)
                wh.store = request.store
                if not _qs(request).exists():
                    wh.is_default = True                        # the first warehouse of a branch is its default
                wh.save()
                if wh.is_default:
                    _qs(request).exclude(pk=wh.pk).filter(is_default=True).update(is_default=False)
            return JsonResponse({"ok": True, "message": f"Warehouse “{wh.name}” saved."})
    else:
        form = WarehouseForm(instance=instance, store=request.store)
    return render(request, "inventory/_warehouse_form.html", {"form": form, "warehouse": instance})


@login_required
@permission_required("tenants.change_warehouse", raise_exception=True)
@require_POST
def warehouse_default(request, pk):
    wh = get_object_or_404(_qs(request), pk=pk)
    with transaction.atomic():
        _qs(request).exclude(pk=wh.pk).filter(is_default=True).update(is_default=False)
        wh.is_default = True
        wh.save(update_fields=["is_default", "updated_at"])
    messages.success(request, f"{wh.name} is now the default warehouse.")
    return redirect("inventory:warehouse_list")
