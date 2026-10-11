"""
Stock service layer. post_stock_movement() is the ONLY function that changes
stock. It:
  * locks the (variant, warehouse) row, so concurrent sales/receipts queue up;
  * refuses to go below zero unless allowed (policy, or the legacy POS path);
  * keeps weighted-average cost per variant+warehouse;
  * appends an immutable StockLedgerEntry;
  * keeps inventory.StockItem.quantity (what the POS reads) in step.

Financial effects are NOT done here: the business documents (GRN, delivery,
transfer, adjustment...) post their own journal entries with the amounts this
module returns.
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.services import create_journal_entry, get_account
from audit.models import AuditLog
from audit.services import log_action
from company.utils import company_of_warehouse
from core.errors import ERPError, InsufficientStock
from core.money import D, q2, q3, q4
from numbering.services import next_reference
from .models import (AdjustmentOrder, AdjustmentOrderItem, Batch, BatchStock, ItemWarehouseCost, StockCount,
                     StockCountLine, StockLedgerEntry, StockPolicy, TransferOrder, TransferOrderItem)

ZERO = Decimal("0")


# ------------------------------------------------------------------ helpers
def inventory_account_key(warehouse) -> str:
    """Which GL inventory account a warehouse's stock sits in."""
    return {
        "RAW_MATERIAL": "raw_inventory",
        "WIP": "wip_inventory",
        "FINISHED_GOODS": "fg_inventory",
    }.get(warehouse.warehouse_type, "inventory")


def allow_negative_for(warehouse) -> bool:
    store = warehouse.store
    company = getattr(store, "company", None)
    if company is None:
        return False
    policy = StockPolicy.objects.filter(company=company).first()
    return bool(policy and policy.allow_negative)


def available_qty(variant, warehouse) -> Decimal:
    from inventory.models import StockItem
    row = StockItem.objects.filter(variant=variant, warehouse=warehouse).first()
    return row.quantity if row else ZERO


def avg_cost(variant, warehouse) -> Decimal:
    row = ItemWarehouseCost.objects.filter(variant=variant, warehouse=warehouse).first()
    if row and row.avg_cost > 0:
        return row.avg_cost
    return q4(variant.cost_price or 0)


def resolve_barcode(code: str):
    """Find a variant by barcode or SKU (used by every scan box)."""
    from catalog.models import ProductVariant
    code = (code or "").strip()
    if not code:
        return None
    return (ProductVariant.objects.select_related("product").filter(barcode=code).first()
            or ProductVariant.objects.select_related("product").filter(sku__iexact=code).first())


def get_or_create_batch(variant, batch_no, *, expiry_date=None, manufacture_date=None):
    batch, created = Batch.objects.get_or_create(
        variant=variant, batch_no=batch_no,
        defaults={"expiry_date": expiry_date, "manufacture_date": manufacture_date})
    return batch


# ------------------------------------------------------------------ the one writer
@transaction.atomic
def post_stock_movement(variant, warehouse, quantity, *, reference_type, reference_no="", unit_cost=None, batch=None,
                        user=None, remarks="", allow_negative=None, posted_at=None, _touch_legacy=True):
    """Move stock in (quantity > 0) or out (quantity < 0). Returns the ledger entry.

    Inbound needs `unit_cost` (defaults to the variant's cost price). Outbound is
    always valued at the current weighted-average cost.
    """
    from inventory.models import StockItem

    quantity = q3(quantity)
    if quantity == 0:
        raise ERPError("Stock movement quantity cannot be zero.")
    if not reference_type:
        raise ERPError("A stock movement needs a reference_type (what caused it).")
    if batch is not None and batch.variant_id != variant.id:
        raise ERPError("The batch belongs to a different item.")

    stock_item, _ = StockItem.objects.select_for_update().get_or_create(variant=variant, warehouse=warehouse)
    cost_row, _ = ItemWarehouseCost.objects.select_for_update().get_or_create(variant=variant, warehouse=warehouse)

    on_hand = stock_item.quantity
    # _touch_legacy=False means StockItem ALREADY includes this movement (backfill only).
    new_balance = on_hand + quantity if _touch_legacy else on_hand
    if allow_negative is None:
        allow_negative = allow_negative_for(warehouse)
    if new_balance < 0 and not allow_negative:
        raise InsufficientStock(
            f"Not enough stock of {variant} in {warehouse.name}: have {on_hand}, need {abs(quantity)}.")

    # ---- weighted average cost
    if quantity > 0:
        in_cost = q4(unit_cost if unit_cost is not None else (variant.cost_price or 0))
        if in_cost < 0:
            raise ERPError("Unit cost cannot be negative.")
        base_qty = max(cost_row.quantity, ZERO)
        if base_qty + quantity > 0:
            cost_row.avg_cost = q4((base_qty * cost_row.avg_cost + quantity * in_cost) / (base_qty + quantity))
        move_cost = in_cost
    else:
        move_cost = cost_row.avg_cost if cost_row.avg_cost > 0 else q4(variant.cost_price or 0)
    cost_row.quantity = new_balance
    cost_row.save(update_fields=["quantity", "avg_cost", "updated_at"])

    # ---- batch balance
    if batch is not None:
        bs, _ = BatchStock.objects.select_for_update().get_or_create(batch=batch, warehouse=warehouse)
        if bs.quantity + quantity < 0 and not allow_negative:
            raise InsufficientStock(f"Not enough stock in batch {batch.batch_no}: have {bs.quantity}.")
        bs.quantity += quantity
        bs.save(update_fields=["quantity", "updated_at"])

    # ---- legacy quantity the POS reads
    if _touch_legacy:
        stock_item.quantity = new_balance
        stock_item.save(update_fields=["quantity", "updated_at"])

    return StockLedgerEntry.objects.create(
        variant=variant, warehouse=warehouse, batch=batch, quantity=quantity, balance_after=new_balance,
        unit_cost=move_cost, value_change=q2(quantity * move_cost), reference_type=reference_type[:30],
        reference_no=str(reference_no)[:40], remarks=remarks[:255],
        user=user if (user is not None and getattr(user, "is_authenticated", False)) else None,
        posted_at=posted_at or timezone.now(),
    )


# ------------------------------------------------------------------ valuation / reports
def stock_valuation(company=None, warehouse=None):
    """Rows of (variant, warehouse, qty, avg_cost, value) from the weighted-average table."""
    qs = ItemWarehouseCost.objects.select_related("variant__product", "warehouse__store").exclude(quantity=0)
    if warehouse is not None:
        qs = qs.filter(warehouse=warehouse)
    if company is not None:
        qs = qs.filter(warehouse__store__company=company)
    rows = [{"variant": r.variant, "warehouse": r.warehouse, "quantity": r.quantity,
             "avg_cost": r.avg_cost, "value": q2(r.quantity * r.avg_cost)} for r in qs]
    return rows, q2(sum((r["value"] for r in rows), ZERO))


def inventory_gl_vs_stock(company):
    """Compare GL inventory balances with the stock valuation, per account."""
    from accounting.reports import balance_of
    rows, _ = stock_valuation(company)
    by_key = {}
    for r in rows:
        key = inventory_account_key(r["warehouse"])
        by_key[key] = by_key.get(key, ZERO) + r["value"]
    out = []
    for key in ("inventory", "raw_inventory", "wip_inventory", "fg_inventory", "inventory_in_transit"):
        out.append({"account": key, "gl": q2(balance_of(company, key)), "stock": q2(by_key.get(key, ZERO))})
    for o in out:
        o["difference"] = q2(o["gl"] - o["stock"])
    return out


def reorder_alerts(company):
    """Items at or below their reorder level (item profile) in total across warehouses."""
    from django.db.models import Sum
    from masters.models import ItemProfile
    out = []
    for prof in ItemProfile.objects.filter(variant__product__store__company=company, reorder_level__gt=0).select_related("variant__product"):
        total = ItemWarehouseCost.objects.filter(variant=prof.variant).aggregate(t=Sum("quantity"))["t"] or ZERO
        if total <= prof.reorder_level:
            out.append({"variant": prof.variant, "on_hand": total, "reorder_level": prof.reorder_level})
    return out


def expiring_batches(company, days=30):
    from datetime import timedelta
    limit = timezone.localdate() + timedelta(days=days)
    return BatchStock.objects.filter(
        batch__expiry_date__isnull=False, batch__expiry_date__lte=limit, quantity__gt=0,
        warehouse__store__company=company).select_related("batch__variant", "warehouse").order_by("batch__expiry_date")


# ------------------------------------------------------------------ transfers
def _items(items):
    clean = []
    for it in items:
        qty = q3(it["quantity"])
        if qty <= 0:
            raise ERPError("Transfer/adjustment quantities must be greater than zero.")
        clean.append({**it, "quantity": qty})
    if not clean:
        raise ERPError("Add at least one item.")
    return clean


@transaction.atomic
def create_transfer(from_warehouse, to_warehouse, items, *, user=None, note=""):
    """items: [{"variant": v, "quantity": 5, "batch": optional}]"""
    if from_warehouse.pk == to_warehouse.pk:
        raise ERPError("Source and destination warehouse must be different.")
    company = company_of_warehouse(from_warehouse)
    if company_of_warehouse(to_warehouse).pk != company.pk:
        raise ERPError("Both warehouses must belong to the same company.")
    order = TransferOrder.objects.create(
        company=company, reference=next_reference(company, "stock_transfer"), from_warehouse=from_warehouse,
        to_warehouse=to_warehouse, note=note,
        created_by=user if (user is not None and getattr(user, "is_authenticated", False)) else None)
    for it in _items(items):
        TransferOrderItem.objects.create(order=order, variant=it["variant"], quantity=it["quantity"], batch=it.get("batch"))
    log_action(AuditLog.CREATED, order, user=user, document_number=order.reference, company=company)
    return order


@transaction.atomic
def submit_transfer(order, user=None):
    from approvals.services import submit_for_approval
    order = TransferOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in (TransferOrder.DRAFT, TransferOrder.REJECTED):
        raise ERPError("Only a draft transfer can be submitted.")
    value = sum((it.quantity * avg_cost(it.variant, order.from_warehouse) for it in order.items.all()), ZERO)
    submit_for_approval(order, "stock_transfer", company=order.company, user=user, amount=q2(value), number=order.reference)
    order.refresh_from_db()
    return order


@transaction.atomic
def dispatch_transfer(order, user=None):
    """Stock leaves the source warehouse; value sits in Inventory In Transit until received."""
    order = TransferOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != TransferOrder.APPROVED:
        raise ERPError("Only an approved transfer can be dispatched.")
    total = ZERO
    for it in order.items.select_related("variant"):
        entry = post_stock_movement(it.variant, order.from_warehouse, -it.quantity, reference_type="TRANSFER_OUT",
                                    reference_no=order.reference, batch=it.batch, user=user,
                                    remarks=f"To {order.to_warehouse.name}")
        it.unit_cost = entry.unit_cost
        it.save(update_fields=["unit_cost"])
        total += it.quantity * entry.unit_cost
    total = q2(total)
    out_key, in_key = inventory_account_key(order.from_warehouse), inventory_account_key(order.to_warehouse)
    if total > 0:
        create_journal_entry(
            order.company, date=timezone.localdate(), narration=f"Transfer out {order.reference}",
            source=("transfer_out", order.pk), user=user, store=order.from_warehouse.store,
            lines=[{"account": "inventory_in_transit", "debit": total}, {"account": out_key, "credit": total}])
    order.status, order.dispatched_at = TransferOrder.IN_TRANSIT, timezone.now()
    order.save(update_fields=["status", "dispatched_at", "updated_at"])
    log_action(AuditLog.OTHER, order, user=user, note="dispatched", document_number=order.reference, company=order.company)
    return order


@transaction.atomic
def receive_transfer(order, user=None, received=None):
    """received: optional {item_id: qty}. Default is the full dispatched quantity.
    A shortage is written off to Stock Adjustment (Gain/Loss) so in-transit clears to zero."""
    order = TransferOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != TransferOrder.IN_TRANSIT:
        raise ERPError("Only an in-transit transfer can be received.")
    received = received or {}
    arrived_value, short_value = ZERO, ZERO
    for it in order.items.select_related("variant"):
        qty = q3(received.get(it.pk, it.quantity))
        if qty < 0 or qty > it.quantity:
            raise ERPError(f"Received quantity for {it.variant} must be between 0 and {it.quantity}.")
        if qty > 0:
            post_stock_movement(it.variant, order.to_warehouse, qty, reference_type="TRANSFER_IN", reference_no=order.reference,
                                unit_cost=it.unit_cost, batch=it.batch, user=user, remarks=f"From {order.from_warehouse.name}")
        it.received_quantity = qty
        it.save(update_fields=["received_quantity"])
        arrived_value += qty * it.unit_cost
        short_value += (it.quantity - qty) * it.unit_cost
    arrived_value, short_value = q2(arrived_value), q2(short_value)
    lines = []
    if arrived_value > 0:
        lines.append({"account": inventory_account_key(order.to_warehouse), "debit": arrived_value})
    if short_value > 0:
        lines.append({"account": "stock_adjustment", "debit": short_value, "description": "Transit shortage"})
    if lines:
        lines.append({"account": "inventory_in_transit", "credit": arrived_value + short_value})
        create_journal_entry(order.company, date=timezone.localdate(), narration=f"Transfer in {order.reference}",
                             source=("transfer_in", order.pk), user=user, store=order.to_warehouse.store, lines=lines)
    order.status, order.received_at = TransferOrder.RECEIVED, timezone.now()
    order.save(update_fields=["status", "received_at", "updated_at"])
    log_action(AuditLog.OTHER, order, user=user, note="received", document_number=order.reference, company=order.company)
    return order


@transaction.atomic
def cancel_transfer(order, user=None):
    order = TransferOrder.objects.select_for_update().get(pk=order.pk)
    if order.status in (TransferOrder.IN_TRANSIT, TransferOrder.RECEIVED, TransferOrder.CANCELLED):
        raise ERPError("A dispatched or received transfer cannot be cancelled.")
    order.status = TransferOrder.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.CANCELLED, order, user=user, document_number=order.reference, company=order.company)
    return order


# ------------------------------------------------------------------ adjustments
@transaction.atomic
def create_adjustment(warehouse, items, *, reason="recount", user=None, note="", source_count=None):
    """items: [{"variant": v, "quantity_change": -2, "unit_cost": optional (increases), "batch": optional}]"""
    company = company_of_warehouse(warehouse)
    order = AdjustmentOrder.objects.create(
        company=company, reference=next_reference(company, "stock_adjustment"), warehouse=warehouse, reason=reason,
        note=note, source_count=source_count,
        created_by=user if (user is not None and getattr(user, "is_authenticated", False)) else None)
    n = 0
    for it in items:
        change = q3(it["quantity_change"])
        if change == 0:
            continue
        AdjustmentOrderItem.objects.create(order=order, variant=it["variant"], quantity_change=change,
                                           unit_cost=q4(it.get("unit_cost") or 0), batch=it.get("batch"))
        n += 1
    if n == 0:
        raise ERPError("An adjustment needs at least one non-zero line.")
    log_action(AuditLog.CREATED, order, user=user, document_number=order.reference, company=company)
    return order


def _adjustment_value(order):
    total = ZERO
    for it in order.items.select_related("variant"):
        cost = it.unit_cost if it.quantity_change > 0 and it.unit_cost > 0 else avg_cost(it.variant, order.warehouse)
        total += abs(it.quantity_change) * cost
    return q2(total)


@transaction.atomic
def submit_adjustment(order, user=None):
    from approvals.services import submit_for_approval
    order = AdjustmentOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in (AdjustmentOrder.DRAFT, AdjustmentOrder.REJECTED):
        raise ERPError("Only a draft adjustment can be submitted.")
    submit_for_approval(order, "stock_adjustment", company=order.company, user=user, amount=_adjustment_value(order),
                        number=order.reference)
    order.refresh_from_db()
    return order


@transaction.atomic
def post_adjustment(order, user=None):
    """Called when the approval completes (see workflows.py). Posts stock and the gain/loss journal."""
    order = AdjustmentOrder.objects.select_for_update().get(pk=order.pk)
    if order.status == AdjustmentOrder.POSTED:
        return order
    if order.status not in (AdjustmentOrder.PENDING, AdjustmentOrder.DRAFT):
        raise ERPError("This adjustment cannot be posted in its current status.")
    gain, loss = ZERO, ZERO
    inv_key = inventory_account_key(order.warehouse)
    for it in order.items.select_related("variant"):
        if it.quantity_change > 0:
            cost = it.unit_cost if it.unit_cost > 0 else avg_cost(it.variant, order.warehouse)
            entry = post_stock_movement(it.variant, order.warehouse, it.quantity_change, reference_type="ADJUSTMENT",
                                        reference_no=order.reference, unit_cost=cost, batch=it.batch, user=user,
                                        remarks=order.get_reason_display())
            gain += entry.value_change
        else:
            entry = post_stock_movement(it.variant, order.warehouse, it.quantity_change, reference_type="ADJUSTMENT",
                                        reference_no=order.reference, batch=it.batch, user=user, remarks=order.get_reason_display())
            loss += -entry.value_change
    net = q2(gain - loss)
    if net != 0:
        lines = ([{"account": inv_key, "debit": net}, {"account": "stock_adjustment", "credit": net}] if net > 0 else
                 [{"account": "stock_adjustment", "debit": -net}, {"account": inv_key, "credit": -net}])
        create_journal_entry(order.company, date=timezone.localdate(), narration=f"Stock adjustment {order.reference}",
                             source=("stock_adjustment", order.pk), user=user, store=order.warehouse.store, lines=lines)
    order.status, order.posted_at = AdjustmentOrder.POSTED, timezone.now()
    order.save(update_fields=["status", "posted_at", "updated_at"])
    log_action(AuditLog.POSTED, order, user=user, document_number=order.reference, company=order.company)
    return order


# ------------------------------------------------------------------ physical count
@transaction.atomic
def open_stock_count(warehouse, *, variants=None, user=None, note=""):
    from inventory.models import StockItem
    company = company_of_warehouse(warehouse)
    count = StockCount.objects.create(
        company=company, reference=next_reference(company, "stock_count"), warehouse=warehouse, note=note,
        created_by=user if (user is not None and getattr(user, "is_authenticated", False)) else None)
    qs = StockItem.objects.filter(warehouse=warehouse).select_related("variant")
    if variants is not None:
        qs = qs.filter(variant__in=variants)
    for si in qs:
        StockCountLine.objects.create(count=count, variant=si.variant, system_quantity=si.quantity)
    log_action(AuditLog.CREATED, count, user=user, document_number=count.reference, company=company)
    return count


@transaction.atomic
def record_count(count, counted: dict, user=None):
    """counted: {variant_id: counted_qty}"""
    count = StockCount.objects.select_for_update().get(pk=count.pk)
    if count.status != StockCount.OPEN:
        raise ERPError("This count is closed.")
    lines = {l.variant_id: l for l in count.lines.all()}
    for variant_id, qty in counted.items():
        line = lines.get(variant_id)
        if line is None:
            raise ERPError(f"Item {variant_id} is not part of this count.")
        qty = q3(qty)
        if qty < 0:
            raise ERPError("Counted quantity cannot be negative.")
        line.counted_quantity = qty
        line.save(update_fields=["counted_quantity"])
    return count


@transaction.atomic
def submit_count(count, user=None):
    """Turns count differences into an adjustment order and sends it for approval.
    The movement uses the CURRENT balance, not the snapshot, so sales made while
    counting are not double-corrected."""
    count = StockCount.objects.select_for_update().get(pk=count.pk)
    if count.status != StockCount.OPEN:
        raise ERPError("This count is already submitted.")
    items = []
    for line in count.lines.select_related("variant"):
        if line.counted_quantity is None:
            continue
        diff = line.counted_quantity - line.system_quantity
        if diff != 0:
            items.append({"variant": line.variant, "quantity_change": diff})
    count.status = StockCount.SUBMITTED
    count.save(update_fields=["status", "updated_at"])
    if not items:
        count.status = StockCount.POSTED
        count.save(update_fields=["status", "updated_at"])
        return count, None
    adjustment = create_adjustment(count.warehouse, items, reason="recount", user=user,
                                   note=f"From stock count {count.reference}", source_count=count)
    adjustment = submit_adjustment(adjustment, user)
    return count, adjustment
