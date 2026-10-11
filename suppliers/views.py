from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from configuration.models import NumberingPrefix, SystemConfiguration
from core.listing import PagedListMixin
from core.mixins import DeleteContextMixin, SimpleListMixin, StoreCrudMixin
from inventory.models import StockItem
from .forms import PurchaseOrderForm, PurchaseOrderItemFormSet, PurchasePaymentForm, SupplierForm
from .models import PurchaseOrder, Supplier


# ---------------------------------------------------------------- Supplier
class SupplierListView(PagedListMixin, ListView):
    """Server-side search / status filter / pagination."""
    filter_params = ("q", "status", "city")
    model = Supplier
    template_name = "suppliers/supplier_list.html"
    context_object_name = "suppliers"

    def get_queryset(self):
        from django.db.models import Count
        qs = Supplier.objects.filter(store=self.request.store).annotate(order_count=Count("purchase_orders"))
        g = self.request.GET
        q = (g.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(company__icontains=q) | Q(contact_person__icontains=q) | Q(phone__icontains=q)
                           | Q(email__icontains=q) | Q(city__icontains=q) | Q(tax_no__icontains=q))
        if g.get("status") in ("active", "inactive"):
            qs = qs.filter(is_active=g["status"] == "active")
        if g.get("city"):
            qs = qs.filter(city__iexact=g["city"])
        return qs.order_by("name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["cities"] = (Supplier.objects.filter(store=self.request.store).exclude(city="").order_by("city")
                         .values_list("city", flat=True).distinct())
        return ctx


class SupplierCreateView(StoreCrudMixin, CreateView):
    model = Supplier
    form_class = SupplierForm
    template_name = "suppliers/supplier_form.html"
    success_url = reverse_lazy("suppliers:supplier_list")
    permission_required = "suppliers.add_supplier"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add supplier", back_url=self.success_url, **kwargs)


class SupplierUpdateView(StoreCrudMixin, UpdateView):
    model = Supplier
    form_class = SupplierForm
    template_name = "suppliers/supplier_form.html"
    success_url = reverse_lazy("suppliers:supplier_list")
    permission_required = "suppliers.change_supplier"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit supplier", back_url=self.success_url, **kwargs)


class SupplierDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Supplier
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("suppliers:supplier_list")
    permission_required = "suppliers.delete_supplier"


# ---------------------------------------------------------------- Purchase order
class PurchaseOrderListView(PagedListMixin, ListView):
    """Server-side search / status filter / pagination."""
    filter_params = ("q", "status", "pay", "warehouse", "df", "dt")
    model = PurchaseOrder
    template_name = "suppliers/purchase_order_list.html"
    context_object_name = "orders"

    def get_queryset(self):
        qs = (
            PurchaseOrder.objects.filter(warehouse__store=self.request.store)
            .select_related("supplier", "warehouse")
            .prefetch_related("items", "payments")
            .order_by("-order_date", "-created_at")
        )
        q, status = self.request.GET.get("q"), self.request.GET.get("status")
        if q:
            qs = qs.filter(Q(reference__icontains=q) | Q(supplier__name__icontains=q) | Q(supplier_ref__icontains=q))
        if status:
            qs = qs.filter(status=status)
        g = self.request.GET
        if g.get("warehouse", "").isdigit():
            qs = qs.filter(warehouse_id=int(g["warehouse"]))
        if g.get("df"):
            qs = qs.filter(order_date__gte=g["df"])
        if g.get("dt"):
            qs = qs.filter(order_date__lte=g["dt"])
        if g.get("pay") in ("paid", "partial", "due"):
            qs = self._payment_filter(qs, g["pay"])
        return qs

    @staticmethod
    def _payment_filter(qs, pay):
        """paid = nothing left to pay (after goods sent back); due = nothing paid yet; partial = in between. Sums are
        sub-queries so the order list keeps one row per order."""
        from decimal import Decimal
        from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum
        from django.db.models.functions import Coalesce
        from .models import PurchasePayment, PurchaseOrderItem, PurchaseReturnItem
        money = DecimalField(max_digits=16, decimal_places=3)

        def total(model, rel, expr):
            sub = model.objects.filter(**{rel: OuterRef("pk")}).order_by().values(rel).annotate(t=Sum(expr)).values("t")
            return Coalesce(Subquery(sub, output_field=money), Decimal("0"), output_field=money)

        line = ExpressionWrapper(F("quantity") * F("cost_price"), output_field=money)
        qs = qs.annotate(_total=total(PurchaseOrderItem, "purchase_order", line),
                         _paid=total(PurchasePayment, "purchase_order", F("amount")),
                         _back=total(PurchaseReturnItem, "purchase_return__purchase_order", line))
        if pay == "paid":
            return qs.filter(_total__gt=0, _total__lte=F("_paid") + F("_back"))
        if pay == "due":
            return qs.filter(_paid__lte=0, _total__gt=F("_back"))
        return qs.filter(_paid__gt=0, _total__gt=F("_paid") + F("_back"))

    def get_context_data(self, **kwargs):
        from tenants.models import Warehouse
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = PurchaseOrder.STATUS_CHOICES
        ctx["warehouses"] = Warehouse.objects.filter(store=self.request.store).order_by("name")
        ctx.update({"pay": self.request.GET.get("pay", ""), "warehouse_id": self.request.GET.get("warehouse", ""),
                    "df": self.request.GET.get("df", ""), "dt": self.request.GET.get("dt", "")})
        return ctx


@login_required
@permission_required("suppliers.add_purchaseorder", raise_exception=True)
def purchase_order_form(request, pk=None):
    store = request.store
    instance = get_object_or_404(PurchaseOrder, pk=pk, warehouse__store=store) if pk else None

    if request.method == "POST":
        if _managed_by_erp(instance):
            messages.error(request, "This order is managed by ERP Procurement (goods receipts, invoices, approvals). "
                                    "Change it there so stock and accounts stay correct.")
            return redirect("suppliers:purchase_detail", pk=instance.pk)
        form = PurchaseOrderForm(request.POST, instance=instance, store=store)
        was_received = instance.status == "received" if instance else False
        formset = PurchaseOrderItemFormSet(request.POST, instance=instance or PurchaseOrder(), prefix="items")

        # nothing is written until BOTH the form and the lines are valid, and the save + stock receipt succeed or fail together
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                order = form.save(commit=False)
                if not order.reference:
                    order.reference = NumberingPrefix.next_reference(NumberingPrefix.PURCHASE)
                    order.created_by = request.user
                order.save()
                formset.instance = order
                formset.save()
                if order.status == "received" and not was_received:
                    _receive_stock(order, user=request.user)

            messages.success(request, f"Purchase order {order.reference} saved.")
            return redirect("suppliers:purchase_list")
    else:
        initial = None
        if not instance:
            initial = {"order_date": None}
            sup = request.GET.get("supplier", "")
            if sup.isdigit() and Supplier.objects.filter(pk=int(sup), store=store, is_active=True).exists():
                initial["supplier"] = int(sup)               # "New purchase" from the supplier list
        form = PurchaseOrderForm(instance=instance, store=store, initial=initial)
        formset = PurchaseOrderItemFormSet(instance=instance, prefix="items")

    return render(request, "suppliers/purchase_order_form.html", {
        "form": form, "formset": formset, "instance": instance, "lines": _lines_payload(formset),
        "initial_forms": formset.initial_form_count(), "order_status": form["status"].value() or "draft"})


def _num(value, places=None) -> str:
    """'5.000' -> '5' for quantities; costs keep two decimals. Anything that is not a number is handed back as typed."""
    from decimal import Decimal, InvalidOperation
    if value in (None, ""):
        return ""
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if places is not None:
        return f"{d:.{places}f}"
    return format(d.normalize(), "f") if d == d.to_integral() or d.is_finite() else str(value)


def _lines_payload(formset) -> list[dict]:
    """The order lines as the screen's JavaScript wants them (the same data on first load, and when a failed save is shown
    again: whatever was typed comes back, with each line's own error). Existing lines come first so their form index
    matches the formset."""
    from catalog.models import ProductVariant
    rows = []
    for f in formset.forms:
        rows.append({"id": f["id"].value() or "", "variant": f["variant"].value() or "", "quantity": f["quantity"].value(),
                     "cost": f["cost_price"].value(), "deleted": bool(f["DELETE"].value()) if "DELETE" in f.fields else False,
                     "error": " ".join(e for errs in f.errors.values() for e in errs)})
    ids = [int(r["variant"]) for r in rows if str(r["variant"]).isdigit()]
    found = {v.pk: v for v in ProductVariant.objects.filter(pk__in=ids).select_related("product", "product__unit")}
    for r in rows:
        v = found.get(int(r["variant"])) if str(r["variant"]).isdigit() else None
        r.update({"name": str(v) if v else "", "sku": v.sku if v else "", "barcode": v.barcode if v else "",
                  "unit": v.product.unit.short_name if v else "",
                  "quantity": _num(r["quantity"]), "cost": _num(r["cost"], places=2)})
    return rows


def _managed_by_erp(order) -> bool:
    """True for purchase orders created through ERP Procurement (they have an `erp` extension row)."""
    return order is not None and getattr(order, "erp", None) is not None


def _receive_stock(purchase_order: PurchaseOrder, user=None):
    """Marking a PO 'received' pushes every line into stock — and, if
    Settings > System Configuration has 'update cost with purchase' on,
    refreshes each variant's cost price too."""
    update_cost = SystemConfiguration.get().update_cost_with_purchase
    for item in purchase_order.items.select_related("variant"):
        stock_item, _ = StockItem.objects.get_or_create(variant=item.variant, warehouse=purchase_order.warehouse)
        stock_item.adjust(item.quantity, reference_type="PO_LEGACY", reference_no=purchase_order.reference, user=user,
                          unit_cost=item.cost_price)
        if update_cost:
            item.variant.cost_price = item.cost_price
            item.variant.save(update_fields=["cost_price", "updated_at"])


@login_required
@permission_required("suppliers.view_purchaseorder", raise_exception=True)
def purchase_order_detail(request, pk):
    order = get_object_or_404(PurchaseOrder, pk=pk, warehouse__store=request.store)
    if request.method == "POST":
        form = PurchasePaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.purchase_order = order
            payment.reference = NumberingPrefix.next_reference(NumberingPrefix.PURCHASE_PAYMENT)
            payment.created_by = request.user
            payment.save()
            messages.success(request, f"Payment {payment.reference} recorded.")
            return redirect("suppliers:purchase_detail", pk=order.pk)
    else:
        form = PurchasePaymentForm(initial={"amount": max(order.due_amount, 0).quantize(__import__("decimal").Decimal("0.01"))})
    from . import purchase_returns as pr
    returns = list(order.returns.prefetch_related("items__variant__product"))
    for r in returns:
        r.value = sum((i.quantity * i.cost_price for i in r.items.all()), 0)
    due = order.due_amount
    erp_managed = _managed_by_erp(order)
    return render(request, "suppliers/purchase_order_detail.html", {
        "order": order, "payment_form": form, "returns": returns, "returned": order.returned_amount, "erp_managed": erp_managed,
        "due_label": "Due" if due >= 0 else "Supplier owes you", "due_abs": abs(due),
        "can_return": order.status == "received" and not erp_managed and request.user.has_perm("suppliers.add_purchasereturn")
                      and any(l["can_send"] > 0 for l in pr.return_lines(order))})


@login_required
@permission_required("suppliers.add_purchasereturn", raise_exception=True)
def purchase_return_form(request, pk):
    from decimal import Decimal, InvalidOperation
    from . import purchase_returns as pr
    order = get_object_or_404(PurchaseOrder.objects.select_related("supplier", "warehouse"), pk=pk, warehouse__store=request.store)
    if order.status != "received" or pr.managed_by_erp(order):
        messages.error(request, "Only a received order that was entered here can be returned to the supplier."
                       if order.status != "received" else "This order is managed by ERP Procurement - return it there.")
        return redirect("suppliers:purchase_detail", pk=pk)
    lines = pr.return_lines(order)
    posted = {}
    reason = ""
    if request.method == "POST":
        reason = request.POST.get("reason", "")
        try:
            quantities = {}
            for l in lines:
                raw = (request.POST.get(f"qty_{l['variant_id']}") or "").strip()
                posted[l["variant_id"]] = raw
                if not raw:
                    continue
                try:
                    q = Decimal(raw)
                except InvalidOperation:
                    raise pr.PurchaseReturnError(f"The quantity for {l['variant']} is not a number.")
                if not q.is_finite() or q < 0:
                    raise pr.PurchaseReturnError(f"The quantity for {l['variant']} is not valid.")
                quantities[l["variant_id"]] = q
            ret = pr.create_return(order, quantities, reason, request.user)
        except pr.PurchaseReturnError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Return {ret.reference} saved - the stock has left {order.warehouse.name}.")
            return redirect("suppliers:purchase_detail", pk=pk)
    return render(request, "suppliers/purchase_return_form.html", {"order": order, "lines": lines, "posted": posted, "reason": reason})
