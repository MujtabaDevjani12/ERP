"""One place that answers "how much did we sell?" so the dashboard and every report agree.

A sale that was partly or fully returned changes status (`partially_returned` / `returned`), so the number of sales has to
count all three states, and the returned goods are then taken off ONCE, on the day the return was made.
"""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Sum
from django.utils import timezone

ZERO = Decimal("0")
COUNTED = ("completed", "partially_returned", "returned")        # sales that really happened (held / cancelled never count)


def _q2(value):
    return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def counted_sales(store):
    from .models import Sale
    return Sale.objects.filter(store=store, status__in=COUNTED)


def _earlier_units(sale_item_ids):
    """{return item id: units of the same line that had already come back before it} - lets part-returns add up to the exact line value."""
    from .models import SaleReturnItem
    done, out = defaultdict(lambda: ZERO), {}
    for ri in SaleReturnItem.objects.filter(sale_item_id__in=sale_item_ids).order_by("sale_return__created_at", "id"):
        out[ri.pk] = done[ri.sale_item_id]
        done[ri.sale_item_id] += ri.quantity
    return out


def returned_items(store, date_from=None, date_to=None, sales=None):
    """One dict per returned line: day (local date of the return), sale_id, customer_id, variant_id, qty, net (money back without tax),
    tax, value (= net + tax, exactly what returns.refund_value gives) and cost (units x the product's cost price)."""
    from .models import SaleReturnItem
    qs = SaleReturnItem.objects.filter(sale_return__sale__store=store).select_related("sale_item__variant", "sale_return", "sale_item__sale")
    if date_from is not None:
        qs = qs.filter(sale_return__created_at__date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(sale_return__created_at__date__lte=date_to)
    if sales is not None:
        qs = qs.filter(sale_item__sale__in=sales)
    from .returns import line_parts
    rows = []
    qs = list(qs)
    ordered = _earlier_units({ri.sale_item_id for ri in qs})
    for ri in qs:
        item = ri.sale_item
        if not item.quantity:
            continue
        net, tax = line_parts(item, ri.quantity, ordered.get(ri.pk, ZERO))
        rows.append({"day": timezone.localtime(ri.sale_return.created_at).date(), "sale_id": item.sale_id, "customer_id": item.sale.customer_id,
                     "variant_id": item.variant_id, "qty": ri.quantity, "net": net, "tax": tax, "value": net + tax,
                     "cost": _q2(ri.quantity * (item.variant.cost_price or ZERO))})
    return rows


def returns_total(store, date_from=None, date_to=None):
    rows = returned_items(store, date_from, date_to)
    return {"value": sum((r["value"] for r in rows), ZERO), "net": sum((r["net"] for r in rows), ZERO),
            "tax": sum((r["tax"] for r in rows), ZERO), "cost": sum((r["cost"] for r in rows), ZERO), "rows": rows}


def sale_return_adjustments(sales):
    """{sale_id: (returned goods value, refunds paid out)} for the sales (only sales that have returns appear)."""
    from payments.models import Refund
    from .models import SaleReturnItem
    from .returns import refund_value
    returned = defaultdict(lambda: ZERO)
    rows = list(SaleReturnItem.objects.filter(sale_item__sale__in=sales).select_related("sale_item"))
    ordered = _earlier_units({ri.sale_item_id for ri in rows})
    for ri in rows:
        item = ri.sale_item
        if item.quantity:
            returned[item.sale_id] += refund_value(item, ri.quantity, ordered.get(ri.pk, ZERO))
    refunded = {r["order_id"]: r["t"] or ZERO for r in Refund.objects.filter(order__in=sales).values("order_id").annotate(t=Sum("amount"))}
    return {sid: (returned.get(sid, ZERO), refunded.get(sid, ZERO)) for sid in set(returned) | set(refunded)}


def unpaid_total(sales, paid_total):
    """What customers still owe on these sales: totals - payments, corrected for goods that came back and money paid back
    (a credit sale that was returned is no longer owed; a refund that was handed over is no longer 'paid')."""
    total = sales.aggregate(t=Sum("total_amount"))["t"] or ZERO
    adj = sale_return_adjustments(sales.filter(status__in=("partially_returned", "returned")))
    correction = sum((ret - ref for ret, ref in adj.values()), ZERO)
    return max(ZERO, total - paid_total - correction)
