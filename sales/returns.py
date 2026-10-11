"""Returns & refunds from the POS / order screens.

Uses the ERP POS layer (pos_erp.process_pos_return: stock back in, refund record, sale status, accounting) when it is
installed; otherwise a small built-in version does the same without the accounting. Both write the same legacy tables
(SaleReturn / SaleReturnItem / payments.Refund), so reports see the same data either way."""
import json
from decimal import ROUND_HALF_UP, Decimal

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from audit.services import log_action
from configuration.models import NumberingPrefix
from payments.models import PaymentMethod, Refund
from register.views import get_active_session
from .models import Sale, SaleItem, SaleReturn, SaleReturnItem
from .pos_stock import stock_effects

ZERO = Decimal("0")
RETURNABLE = ("completed", "partially_returned")


class ReturnError(Exception):
    """Something the cashier can fix (shown as a normal message)."""


def _q2(v):
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def returnable_map(sale):
    """{sale_item_id: quantity still returnable}"""
    out = {}
    for item in sale.items.all():
        returned = sum((r.quantity for r in item.return_items.all()), ZERO)
        out[item.pk] = item.quantity - returned
    return out


def refund_value(sale_item, qty):
    """Money given back for `qty` of this line: its share of (price - discount) plus its share of the tax."""
    share = Decimal(qty) / sale_item.quantity
    return _q2((sale_item.quantity * sale_item.unit_price - sale_item.discount_amount) * share) + _q2(sale_item.tax_amount * share)


def refundable_balance(sale):
    """What the customer actually paid and has not had back yet (a credit sale can only be refunded what was paid)."""
    paid = sale.payments.exclude(status="voided").aggregate(t=Sum("amount"))["t"] or ZERO
    refunded = sale.refunds.aggregate(t=Sum("amount"))["t"] or ZERO
    return _q2(paid - refunded)


def _basic_return(sale, picked, reason, user, session, refund_method, restock):
    """Fallback when the ERP POS layer is not installed (no accounting posting, no approval threshold)."""
    from inventory.models import StockItem
    with transaction.atomic():
        sale = Sale.objects.select_for_update().get(pk=sale.pk)
        if sale.status not in RETURNABLE:
            raise ReturnError(f"A sale that is '{sale.get_status_display()}' cannot be returned.")
        left = returnable_map(sale)
        total = ZERO
        sale_return = SaleReturn.objects.create(reference=NumberingPrefix.next_reference(NumberingPrefix.SALE_RETURN), sale=sale,
                                                reason=reason, processed_by=user)
        for sale_item, qty in picked:
            if qty > left[sale_item.pk]:
                raise ReturnError(f"{sale_item.variant}: only {left[sale_item.pk]} can still be returned.")
            SaleReturnItem.objects.create(sale_return=sale_return, sale_item=sale_item, quantity=qty)
            total += refund_value(sale_item, qty)
            if restock:
                for stock_variant, back in stock_effects(sale_item.variant, qty):
                    stock, _ = StockItem.objects.select_for_update().get_or_create(variant=stock_variant, warehouse=sale.warehouse)
                    stock.adjust(back, reference_type="POS_RETURN", reference_no=sale_return.reference, user=user, remarks=reason[:200])
        if total > 0 and refund_method:
            Refund.objects.create(reference=NumberingPrefix.next_reference("refund"), order=sale, amount=total, method=refund_method,
                                  reason=reason, register_session=session, processed_by=user)
        fully = all(v <= 0 for v in returnable_map(sale).values())
        sale.status = "returned" if fully else "partially_returned"
        sale.save(update_fields=["status", "updated_at"])
        log_action(AuditLog.CREATED, sale_return, user=user, document_number=sale_return.reference, note=f"refund {total}")
    return sale_return


def sale_lines(sale):
    """Every line of a sale with how much of it can still be taken back and what one unit refunds."""
    left = returnable_map(sale)
    lines = []
    for item in sale.items.select_related("variant__product"):
        lines.append({"item": item, "left": left[item.pk], "unit_refund": refund_value(item, item.quantity) / item.quantity if item.quantity else ZERO})
    return lines


def execute_return(request, sale, raw_qty, reason, method, restock, exchange=False):
    """Validate and perform a return. `raw_qty` is {sale_item_pk: text}. Returns (reference, refund_total).
    Raises ReturnError (a message the cashier can fix) or PermissionDenied (return too large for this user to approve)."""
    methods = list(PaymentMethod.active_for(request.store))
    session = get_active_session(request)
    lines = sale_lines(sale)
    balance = refundable_balance(sale)
    picked = []
    for line in lines:
        raw = str(raw_qty.get(line["item"].pk, "") or "0").strip()
        try:
            qty = Decimal(raw or "0")
        except Exception:                                 # noqa: BLE001
            raise ReturnError(f"Quantity for {line['item'].variant} is not a number.")
        if qty < 0:
            raise ReturnError("Quantities cannot be negative.")
        if qty > 0:
            if qty > line["left"]:
                raise ReturnError(f"{line['item'].variant}: only {line['left']} can still be returned.")
            picked.append((line["item"], qty))
    if not picked:
        raise ReturnError("Select at least one item to return.")
    if not reason:
        raise ReturnError("A reason is required for a return.")
    if method not in {m.code for m in methods}:
        raise ReturnError("Choose how the customer is refunded.")
    refund_total = sum((refund_value(i, q) for i, q in picked), ZERO)
    if refund_total > balance:
        raise ReturnError(f"The customer has only paid {balance} that can be refunded; this return is worth {refund_total}. "
                          f"Return fewer items or settle the difference on the customer's account.")
    if method == "cash":
        if session is None:
            raise ReturnError("Open a register first - a cash refund is taken out of the till.")
        if refund_total > session.expected_cash:
            raise ReturnError(f"The till holds {session.expected_cash}, which is less than the {refund_total} cash refund.")
    if apps.is_installed("pos_erp"):
        from core.errors import ERPError
        from pos_erp.services import process_pos_return
        try:
            pos_return = process_pos_return(sale, [{"sale_item": i, "quantity": q} for i, q in picked], reason, user=request.user,
                                            session=session, refund_method=method, restock=restock)
        except ERPError as exc:
            raise ReturnError(str(exc))
        reference = pos_return.sale_return.reference
    else:
        reference = _basic_return(sale, picked, reason, request.user, session, method, restock).reference
    return reference, refund_total


@login_required
@permission_required("sales.add_salereturn", raise_exception=True)
def pos_find_sale(request):
    """POS 'Return' button: find a sale by invoice number (the POS then shows it in a window, no page change)."""
    ref = request.GET.get("ref", "").strip()
    if not ref:
        return JsonResponse({"ok": False, "error": "Enter an invoice number."}, status=400)
    sale = Sale.objects.filter(store=request.store, reference__iexact=ref).first() or \
        Sale.objects.filter(store=request.store, reference__icontains=ref).order_by("-created_at").first()
    if sale is None:
        return JsonResponse({"ok": False, "error": f"No sale found for “{ref}”."}, status=404)
    if sale.status not in RETURNABLE:
        return JsonResponse({"ok": False, "error": f"{sale.reference} is {sale.get_status_display().lower()} and cannot be returned."}, status=400)
    return JsonResponse({"ok": True, "sale_id": sale.pk, "return_url": reverse("sales:sale_return", args=[sale.pk]), "reference": sale.reference})


def _money(v):
    return str(_q2(v))


@login_required
def pos_orders(request):
    """Feeds the POS 'Orders' window: this branch's recent sales (held ones excluded), searchable, 20 per page."""
    if not request.user.has_perm("sales.view_sale"):
        return JsonResponse({"ok": False, "error": "You do not have permission to view orders."}, status=403)
    qs = Sale.objects.filter(store=request.store).exclude(status="held").select_related("customer", "biller").prefetch_related("payments").annotate(item_count=Count("items"))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(reference__icontains=q) | Q(customer__name__icontains=q) | Q(customer__phone__icontains=q))
    status = request.GET.get("status", "").strip()
    if status in {"completed", "returned", "partially_returned", "cancelled"}:
        qs = qs.filter(status=status)
    try:
        page = max(1, int(request.GET.get("page", "1")))
    except ValueError:
        page = 1
    size = 20
    rows = list(qs.order_by("-created_at")[(page - 1) * size:page * size + 1])
    can_return = request.user.has_perm("sales.add_salereturn")
    return JsonResponse({"ok": True, "has_more": len(rows) > size, "page": page, "sales": [
        {"id": s.pk, "reference": s.reference, "customer": s.customer.name if s.customer else "Walk-in customer",
         "total": _money(s.total_amount), "status": s.status, "status_label": s.get_status_display(),
         "date": s.created_at.strftime("%d %b, %H:%M"), "cashier": s.biller.get_full_name() or s.biller.username,
         "items": s.item_count, "can_return": can_return and s.status in RETURNABLE}
        for s in rows[:size]]})


@login_required
def pos_sale_info(request, pk):
    """Everything the POS window needs to show one sale and to take items back from it."""
    if not request.user.has_perm("sales.view_sale"):
        return JsonResponse({"ok": False, "error": "You do not have permission to view orders."}, status=403)
    sale = Sale.objects.filter(pk=pk, store=request.store).select_related("customer", "biller").first()
    if sale is None:
        return JsonResponse({"ok": False, "error": "Sale not found."}, status=404)
    session = get_active_session(request)
    methods = list(PaymentMethod.active_for(request.store))
    default = (session.register.default_payment_method if session else "") or "cash"
    if default not in {m.code for m in methods} and methods:
        default = methods[0].code
    paid = sum((p.amount for p in sale.payments.exclude(status="voided")), ZERO)
    can_return = request.user.has_perm("sales.add_salereturn") and sale.status in RETURNABLE
    return JsonResponse({"ok": True, "sale": {
        "id": sale.pk, "reference": sale.reference, "status": sale.status, "status_label": sale.get_status_display(),
        "customer": sale.customer.name if sale.customer else "Walk-in customer", "date": sale.created_at.strftime("%d %b %Y, %H:%M"),
        "cashier": sale.biller.get_full_name() or sale.biller.username, "note": sale.sale_note,
        "subtotal": _money(sale.subtotal), "discount": _money(sale.discount_amount), "tax": _money(sale.tax_amount),
        "shipping": _money(sale.shipping_amount), "total": _money(sale.total_amount), "paid": _money(paid),
        "due": _money(sale.total_amount - paid),
        "payments": [{"method": p.get_method_display(), "amount": _money(p.amount)} for p in sale.payments.exclude(status="voided")],
        "lines": [{"id": l["item"].pk, "name": str(l["item"].variant), "sku": l["item"].variant.sku, "qty": str(l["item"].quantity.normalize()),
                   "unit_price": _money(l["item"].unit_price), "line_total": _money(l["item"].line_total),
                   "left": str(l["left"].normalize()), "returned": str((l["item"].quantity - l["left"]).normalize()),
                   "unit_refund": _money(l["unit_refund"])} for l in sale_lines(sale)],
        "can_return": can_return, "methods": [{"code": m.code, "name": m.name} for m in methods], "default_method": default,
        "balance": _money(refundable_balance(sale)), "cash_in_till": _money(session.expected_cash) if session else None,
        "print_url": reverse("sales:invoice_print", args=[sale.pk]), "order_url": reverse("sales:sale_detail", args=[sale.pk]),
    }})


@login_required
@permission_required("sales.add_salereturn", raise_exception=True)
@require_POST
def pos_do_return(request):
    """Take items back from a sale without leaving the POS. Body: {sale_id, qty: {line_id: n}, reason, method, restock, exchange}."""
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"ok": False, "error": "Bad request."}, status=400)
    sale = Sale.objects.filter(pk=body.get("sale_id") or 0, store=request.store).select_related("customer").first()
    if sale is None:
        return JsonResponse({"ok": False, "error": "Sale not found."}, status=404)
    if sale.status not in RETURNABLE:
        return JsonResponse({"ok": False, "error": f"{sale.reference} is {sale.get_status_display().lower()} and cannot be returned."}, status=400)
    exchange = bool(body.get("exchange"))
    reason = str(body.get("reason") or "").strip() or ("Exchange" if exchange else "")
    qty = {}
    for k, v in (body.get("qty") or {}).items():
        try:
            qty[int(k)] = v
        except (TypeError, ValueError):
            continue
    try:
        reference, refund = execute_return(request, sale, qty, reason, str(body.get("method") or "cash"), bool(body.get("restock", True)), exchange)
    except PermissionDenied as exc:
        return JsonResponse({"ok": False, "error": str(exc) or "You are not allowed to approve a return of this size."}, status=403)
    except ReturnError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "return": reference, "sale": sale.reference, "refund": _money(refund), "exchange": exchange})


@login_required
@permission_required("sales.add_salereturn", raise_exception=True)
def sale_return_view(request, pk):
    sale = get_object_or_404(Sale.objects.select_related("customer", "store"), pk=pk, store=request.store)
    if sale.status not in RETURNABLE:
        messages.error(request, f"{sale.reference} is {sale.get_status_display().lower()} and cannot be returned.")
        return redirect("sales:sale_detail", pk=pk)

    methods = list(PaymentMethod.active_for(request.store))
    session = get_active_session(request)
    lines = sale_lines(sale)
    balance = refundable_balance(sale)
    exchange = (request.POST.get("exchange") or request.GET.get("exchange")) == "1"        # an exchange = take the goods back, then sell the new ones
    form = {"reason": "Exchange" if exchange else "", "method": (session.register.default_payment_method if session else "") or "cash", "restock": True, "qty": {}}

    if request.method == "POST":
        form.update(reason=request.POST.get("reason", "").strip(), method=request.POST.get("refund_method", "cash"),
                    restock=request.POST.get("restock") == "on")
        for line in lines:
            form["qty"][line["item"].pk] = (request.POST.get(f"qty_{line['item'].pk}") or "0").strip()
        try:
            reference, refund_total = execute_return(request, sale, form["qty"], form["reason"], form["method"], form["restock"], exchange)
            messages.success(request, f"Return {reference} saved. Refund: {refund_total}.")
            if exchange:                                                                   # carry on at the POS with the new items
                return redirect(reverse("dashboard:pos") + f"?exchange_return={reference}")
            return redirect("sales:sale_detail", pk=pk)
        except PermissionDenied as exc:
            messages.error(request, str(exc) or "You are not allowed to approve a return of this size.")
        except ReturnError as exc:
            messages.error(request, str(exc))

    return render(request, "sales/return_form.html", {"sale": sale, "lines": lines, "methods": methods, "balance": balance, "form": form, "exchange": exchange})
