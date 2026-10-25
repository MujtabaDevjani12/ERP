import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Count, F, Q
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from configuration.models import NumberingPrefix
from core.listing import PagedListMixin
from core.mixins import DeleteContextMixin, SimpleListMixin
from tenants.models import Warehouse
from .models import Rack, StockAdjustment, StockAdjustmentItem, StockItem


class StockListView(PagedListMixin, LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """Server-side search / warehouse + low-stock filters / pagination."""
    filter_params = ("q", "low", "warehouse", "category")
    model = StockItem
    permission_required = "inventory.view_stockitem"
    template_name = "inventory/stock_list.html"
    context_object_name = "stock_items"

    def get_queryset(self):
        qs = StockItem.objects.filter(warehouse__store=self.request.store).select_related(
            "variant", "variant__product", "warehouse"
        )
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(Q(variant__sku__icontains=q) | Q(variant__barcode__icontains=q) | Q(variant__product__name__icontains=q))
        if self.request.GET.get("warehouse"):
            qs = qs.filter(warehouse_id=self.request.GET["warehouse"])
        if self.request.GET.get("category", "").isdigit():
            qs = qs.filter(variant__product__category_id=int(self.request.GET["category"]))
        if self.request.GET.get("low") == "1":
            qs = qs.filter(quantity__lte=F("variant__product__alert_quantity"), variant__product__item_type="standard")
        return qs.order_by("variant__product__name", "warehouse__name", "pk")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from catalog.models import Category
        ctx["warehouses"] = Warehouse.objects.filter(store=self.request.store).order_by("name")
        ctx["categories"] = Category.objects.filter(store=self.request.store).order_by("name")
        return ctx


@login_required
@permission_required("inventory.add_stockadjustment", raise_exception=True)
def quantity_adjustment_view(request):
    """The flagship scan-to-adjust screen: scan/search a product, type the
    new (or +/-) quantity, keep scanning — one Save writes the whole
    batch as a single StockAdjustment + items and updates live stock."""
    store = request.store
    warehouses = store.warehouses.all()

    if request.method == "POST":
        warehouse_id = request.POST.get("warehouse")
        reason = request.POST.get("reason", "recount")
        note = request.POST.get("note", "")
        rows = json.loads(request.POST.get("rows_json", "[]"))

        if not rows:
            messages.error(request, "No items were added.")
            return redirect("inventory:quantity_adjustment")

        # validate everything first so a bad row cannot leave a half-saved adjustment behind
        clean_rows = []
        try:
            if not store.warehouses.filter(pk=warehouse_id).exists():
                raise ValueError("Choose a warehouse of this branch.")
            for row in rows:
                change = Decimal(str(row["change"]))
                if change == 0:
                    continue
                clean_rows.append((row["variant_id"], change))
        except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
            messages.error(request, f"Could not read the adjustment rows: {exc}")
            return redirect("inventory:quantity_adjustment")
        if not clean_rows:
            messages.error(request, "Every row has a zero change - nothing to save.")
            return redirect("inventory:quantity_adjustment")

        with transaction.atomic():
            adjustment = StockAdjustment.objects.create(
                reference=NumberingPrefix.next_reference(NumberingPrefix.QUANTITY_ADJUSTMENT),
                warehouse_id=warehouse_id,
                reason=reason,
                note=note,
                created_by=request.user,
            )
            for variant_id, change in clean_rows:
                stock_item, _ = StockItem.objects.select_for_update().get_or_create(variant_id=variant_id, warehouse_id=warehouse_id)
                before = stock_item.quantity
                StockAdjustmentItem.objects.create(
                    adjustment=adjustment, variant_id=variant_id,
                    quantity_before=before, quantity_change=change,
                )
                stock_item.adjust(change, reference_type="POS_ADJUSTMENT", reference_no=adjustment.reference, user=request.user,
                                  remarks=f"{reason} {note}"[:200])
        rows = clean_rows

        messages.success(request, f"Adjustment {adjustment.reference} saved — {len(rows)} item(s) updated.")
        return redirect("inventory:adjustment_list")

    return render(request, "inventory/quantity_adjustment.html", {"warehouses": warehouses, "reasons": StockAdjustment.REASON_CHOICES})


class StockAdjustmentListView(PagedListMixin, LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """Server-side search / pagination."""
    filter_params = ("q",)
    model = StockAdjustment
    permission_required = "inventory.view_stockadjustment"
    template_name = "inventory/adjustment_list.html"
    context_object_name = "adjustments"

    def get_queryset(self):
        qs = (StockAdjustment.objects.filter(warehouse__store=self.request.store).select_related("warehouse", "created_by")
              .annotate(item_count=Count("items")).order_by("-created_at"))
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(Q(reference__icontains=q) | Q(warehouse__name__icontains=q))
        return qs


# ---------------------------------------------------------------- Racks
class RackListView(LoginRequiredMixin, SimpleListMixin, ListView):
    model = Rack
    page_title = "Racks"
    add_url_name = "inventory:rack_add"
    edit_url_name = "inventory:rack_edit"
    delete_url_name = "inventory:rack_delete"

    def get_queryset(self):
        return Rack.objects.filter(warehouse__store=self.request.store)


class RackCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Rack
    permission_required = "inventory.add_rack"
    fields = ["warehouse", "name"]
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("inventory:rack_list")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["warehouse"].queryset = self.request.store.warehouses.all()
        return form

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add rack", back_url=self.success_url, **kwargs)


class RackUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Rack
    permission_required = "inventory.change_rack"
    fields = ["warehouse", "name"]
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("inventory:rack_list")

    def get_queryset(self):
        return Rack.objects.filter(warehouse__store=self.request.store)

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["warehouse"].queryset = self.request.store.warehouses.all()
        return form

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit rack", back_url=self.success_url, **kwargs)


class RackDeleteView(LoginRequiredMixin, PermissionRequiredMixin, DeleteContextMixin, DeleteView):
    model = Rack
    permission_required = "inventory.delete_rack"
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("inventory:rack_list")

    def get_queryset(self):
        return Rack.objects.filter(warehouse__store=self.request.store)
