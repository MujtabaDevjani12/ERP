"""Screens for stock transfers (list, new, detail, send / receive / cancel / delete, product lookup)."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import ListView

from catalog.models import ProductVariant
from core.listing import PagedListMixin
from tenants.models import Warehouse
from . import transfers as svc
from .models import StockItem, StockTransfer


def _fmt(value):
    """10.000 -> '10', 2.500 -> '2.5' (Decimal.normalize() would give '1E+1')."""
    if value is None:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _transfers(request):
    return StockTransfer.objects.filter(from_warehouse__store=request.store).select_related("from_warehouse", "to_warehouse", "created_by")


class TransferListView(PagedListMixin, LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "inventory.view_stocktransfer"
    template_name = "inventory/transfer_list.html"
    context_object_name = "transfers"
    filter_params = ("q", "status", "from", "to")

    def get_queryset(self):
        qs = _transfers(self.request).annotate(n_items=Count("items"), n_qty=Sum("items__quantity"))
        g = self.request.GET
        if g.get("status"):
            qs = qs.filter(status=g["status"])
        if g.get("from"):
            qs = qs.filter(from_warehouse_id=g["from"])
        if g.get("to"):
            qs = qs.filter(to_warehouse_id=g["to"])
        if (g.get("q") or "").strip():
            qs = qs.filter(Q(reference__icontains=g["q"].strip()) | Q(note__icontains=g["q"].strip()))
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        counts = {r["status"]: r["n"] for r in _transfers(self.request).values("status").annotate(n=Count("id"))}
        ctx.update({
            "status_choices": StockTransfer.STATUS_CHOICES,
            "warehouses": Warehouse.objects.filter(store=self.request.store).order_by("name"),
            "n_pending": counts.get("draft", 0), "n_transit": counts.get("in_transit", 0), "n_done": counts.get("received", 0),
        })
        return ctx


@login_required
@permission_required("inventory.add_stocktransfer", raise_exception=True)
def transfer_new(request):
    warehouses = list(Warehouse.objects.filter(store=request.store).order_by("-is_default", "name"))
    if request.method == "POST":
        try:
            by_id = {w.pk: w for w in warehouses}
            try:
                frm, to = by_id[int(request.POST.get("from_warehouse", 0))], by_id[int(request.POST.get("to_warehouse", 0))]
            except (KeyError, ValueError):
                raise svc.TransferError("Choose the source and the destination warehouse.")
            try:
                rows = json.loads(request.POST.get("lines_json") or "[]")
                lines = [(r["variant_id"], r["quantity"]) for r in rows]
            except (ValueError, KeyError, TypeError):
                raise svc.TransferError("Could not read the product list. Please try again.")
            mode = request.POST.get("mode", "pending")
            if mode not in ("pending", "send", "complete"):
                mode = "pending"
            transfer = svc.create_transfer(store=request.store, user=request.user, from_warehouse=frm, to_warehouse=to, lines=lines,
                                           note=request.POST.get("note", ""), mode=mode)
        except svc.TransferError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Transfer {transfer.reference} created.")
            return redirect("inventory:transfer_detail", pk=transfer.pk)
    return render(request, "inventory/transfer_form.html", {
        "warehouses": warehouses,
        "posted": json.dumps({"from": request.POST.get("from_warehouse", ""), "to": request.POST.get("to_warehouse", ""), "note": request.POST.get("note", ""),
                              "lines": request.POST.get("lines_json", "[]")}).replace("<", "\\u003c") if request.method == "POST" else "null",
    })


@login_required
@permission_required("inventory.view_stocktransfer", raise_exception=True)
def transfer_detail(request, pk):
    transfer = get_object_or_404(_transfers(request), pk=pk)
    items = list(transfer.items.select_related("variant", "variant__product").order_by("variant__product__name"))
    on_hand = {r.variant_id: r.quantity for r in StockItem.objects.filter(warehouse=transfer.from_warehouse, variant_id__in=[i.variant_id for i in items])}
    for it in items:
        it.available = on_hand.get(it.variant_id, 0)
        it.short = transfer.status == "draft" and it.available < it.quantity
    steps = [{"label": "Pending", "state": "done"}, {"label": "In transit", "state": "todo"}, {"label": "Completed", "state": "todo"}]
    if transfer.status == "in_transit":
        steps[1]["state"], steps[0]["state"] = "current", "done"
    elif transfer.status == "received":
        steps = [{"label": s["label"], "state": "done"} for s in steps]
    elif transfer.status == "draft":
        steps[0]["state"] = "current"
    elif transfer.status == "cancelled":
        steps = [{"label": "Pending", "state": "done"}, {"label": "Cancelled", "state": "current"}]
    return render(request, "inventory/transfer_detail.html", {
        "transfer": transfer, "items": items, "steps": steps,
        "total_label": f"{_fmt(sum((i.quantity for i in items), 0))} units in {len(items)} product{'' if len(items) == 1 else 's'}", "can_change": request.user.has_perm("inventory.change_stocktransfer"),
        "can_delete": request.user.has_perm("inventory.delete_stocktransfer") and transfer.status in ("draft", "cancelled"),
    })


def _act(request, pk, fn, done):
    transfer = get_object_or_404(_transfers(request), pk=pk)
    try:
        fn(transfer, user=request.user)
    except svc.TransferError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, done.format(ref=transfer.reference))
    return redirect("inventory:transfer_detail", pk=pk)


@login_required
@permission_required("inventory.change_stocktransfer", raise_exception=True)
@require_POST
def transfer_send(request, pk):
    return _act(request, pk, svc.send_transfer, "{ref} is on its way - the stock has left the source warehouse.")


@login_required
@permission_required("inventory.change_stocktransfer", raise_exception=True)
@require_POST
def transfer_receive(request, pk):
    return _act(request, pk, svc.receive_transfer, "{ref} received - the stock is now in the destination warehouse.")


@login_required
@permission_required("inventory.change_stocktransfer", raise_exception=True)
@require_POST
def transfer_cancel(request, pk):
    return _act(request, pk, svc.cancel_transfer, "{ref} cancelled.")


@login_required
@permission_required("inventory.delete_stocktransfer", raise_exception=True)
@require_POST
def transfer_delete(request, pk):
    transfer = get_object_or_404(_transfers(request), pk=pk)
    if transfer.status not in ("draft", "cancelled"):
        messages.error(request, "Only a pending or cancelled transfer can be deleted.")
        return redirect("inventory:transfer_detail", pk=pk)
    ref = transfer.reference
    transfer.delete()
    messages.success(request, f"Transfer {ref} deleted.")
    return redirect("inventory:transfer_list")


@login_required
@permission_required("inventory.add_stocktransfer", raise_exception=True)
@require_GET
def transfer_lookup(request):
    """Product search for the transfer form: stock shown is the stock in the *source* warehouse."""
    q = request.GET.get("q", "").strip()
    wh = request.GET.get("warehouse", "")
    variants = ProductVariant.objects.filter(product__store=request.store, is_active=True, product__is_active=True)
    if q:
        variants = variants.filter(Q(barcode__iexact=q) | Q(barcode__icontains=q) | Q(sku__icontains=q) | Q(name__icontains=q) | Q(product__name__icontains=q))
    variants = list(variants.select_related("product").order_by("product__name", "name")[:15])
    stock = {}
    if wh.isdigit():
        stock = {r.variant_id: r.quantity for r in StockItem.objects.filter(warehouse_id=int(wh), warehouse__store=request.store, variant_id__in=[v.pk for v in variants])}
    return JsonResponse({"results": [{
        "id": v.pk, "name": str(v), "sku": v.sku, "barcode": v.barcode, "stock": _fmt(stock.get(v.pk)),
    } for v in variants]})
