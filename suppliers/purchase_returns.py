"""Sending received goods back to a supplier: stock leaves the warehouse and the amount we owe the supplier goes down.
Only for orders entered on the normal purchase screens - an order created through ERP Procurement is returned there (its own approvals / accounts)."""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum

from configuration.models import NumberingPrefix
from inventory.models import StockItem
from .models import PurchaseReturn, PurchaseReturnItem

ZERO = Decimal("0")


class PurchaseReturnError(Exception):
    """A rule was broken; the message is safe to show."""


def _n(v):
    return format(Decimal(v).normalize(), "f")


def managed_by_erp(order):
    return getattr(order, "erp", None) is not None


def return_lines(order):
    """One row per product on the order: how much came in, how much already went back, what can still go, and the average cost."""
    rows = {}
    for item in order.items.select_related("variant", "variant__product"):
        r = rows.setdefault(item.variant_id, {"variant": item.variant, "received": ZERO, "value": ZERO})
        r["received"] += item.quantity
        r["value"] += item.quantity * item.cost_price
    sent_back = {r["variant_id"]: r["q"] for r in PurchaseReturnItem.objects.filter(purchase_return__purchase_order=order).values("variant_id").annotate(q=Sum("quantity"))}
    on_hand = {s.variant_id: s.quantity for s in StockItem.objects.filter(warehouse=order.warehouse, variant_id__in=list(rows))}
    out = []
    for vid, r in rows.items():
        returned = sent_back.get(vid, ZERO)
        cost = (r["value"] / r["received"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if r["received"] else ZERO
        left = max(r["received"] - returned, ZERO)
        out.append({"variant": r["variant"], "variant_id": vid, "received": r["received"], "returned": returned, "left": left, "cost": cost,
                    "on_hand": on_hand.get(vid, ZERO), "can_send": min(left, on_hand.get(vid, ZERO))})
    return sorted(out, key=lambda x: str(x["variant"]))


@transaction.atomic
def create_return(order, quantities, reason, user):
    """quantities: {variant_id: Decimal}. All-or-nothing."""
    if order.status != "received":
        raise PurchaseReturnError("Only a received order can be returned to the supplier.")
    if managed_by_erp(order):
        raise PurchaseReturnError("This order is managed by ERP Procurement - return it there so stock and accounts stay correct.")
    if not (reason or "").strip():
        raise PurchaseReturnError("A reason is required for a return.")
    lines = {l["variant_id"]: l for l in return_lines(order)}
    picked = []
    for vid, qty in quantities.items():
        if qty <= 0:
            continue
        line = lines.get(vid)
        if line is None:
            raise PurchaseReturnError("One of the products is not on this order.")
        if qty > line["left"]:
            raise PurchaseReturnError(f"{line['variant']}: only {_n(line['left'])} can still be returned.")
        picked.append((line, qty))
    if not picked:
        raise PurchaseReturnError("Choose at least one product to send back.")
    ret = PurchaseReturn.objects.create(reference=NumberingPrefix.next_reference(NumberingPrefix.PURCHASE_RETURN), purchase_order=order,
                                        reason=reason.strip()[:255], created_by=user)
    for line, qty in sorted(picked, key=lambda p: p[0]["variant_id"]):               # fixed lock order
        row = StockItem.objects.select_for_update().filter(variant=line["variant"], warehouse=order.warehouse).first()
        have = row.quantity if row else ZERO
        if have < qty:
            raise PurchaseReturnError(f"{line['variant']}: only {_n(have)} is in {order.warehouse.name} now, so {_n(qty)} cannot be sent back.")
        row.adjust(-qty, reference_type="PURCHASE_RETURN", reference_no=ret.reference, user=user, remarks=f"Returned to {order.supplier.name}")
        PurchaseReturnItem.objects.create(purchase_return=ret, variant=line["variant"], quantity=qty, cost_price=line["cost"])
    from stock.gl import post_legacy_return
    post_legacy_return(ret, user=user)
    return ret
