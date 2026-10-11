"""Stock transfers between warehouses of one branch.

pending (nothing moved) -> in transit (left the source) -> completed (arrived at the destination); a pending or in-transit
transfer can be cancelled (in-transit stock goes back to the source). Every stock change goes through StockItem.adjust(), so
with the ERP stock module installed each movement lands in the audited stock ledger (TRANSFER_OUT / TRANSFER_IN)."""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from configuration.models import NumberingPrefix
from .models import StockItem, StockTransfer, StockTransferItem

PENDING, IN_TRANSIT, COMPLETED, CANCELLED = "draft", "in_transit", "received", "cancelled"


def _n(value):
    return format(Decimal(value).normalize(), "f")


class TransferError(Exception):
    """A rule of the transfer was broken; the message is safe to show to the user."""


def _unit_cost(variant, warehouse):
    """What one unit costs where it is leaving: ERP weighted-average cost when available, else the variant cost price."""
    try:
        from stock.models import ItemWarehouseCost
        row = ItemWarehouseCost.objects.filter(variant=variant, warehouse=warehouse).first()
        if row and row.avg_cost > 0:
            return row.avg_cost
    except Exception:  # noqa: BLE001 - the ERP module is optional
        pass
    return variant.cost_price or Decimal("0")


def clean_lines(lines):
    """[(variant_id, qty)] -> merged, validated list. Raises TransferError for a bad quantity or an empty list."""
    merged = {}
    for variant_id, qty in lines:
        try:
            qty = Decimal(str(qty))
            variant_id = int(variant_id)
        except Exception:  # noqa: BLE001
            raise TransferError("A transfer line has an invalid product or quantity.")
        if qty <= 0:
            raise TransferError("Every quantity must be more than zero.")
        merged[variant_id] = merged.get(variant_id, Decimal("0")) + qty
    if not merged:
        raise TransferError("Add at least one product to transfer.")
    return sorted(merged.items())                 # fixed order: rows are always locked in the same order


@transaction.atomic
def create_transfer(*, store, user, from_warehouse, to_warehouse, lines, note="", mode="pending"):
    """mode: pending (just record it), send (take stock out now) or complete (send and receive at once)."""
    if from_warehouse.pk == to_warehouse.pk:
        raise TransferError("Choose two different warehouses.")
    if from_warehouse.store_id != store.pk or to_warehouse.store_id != store.pk:
        raise TransferError("Both warehouses must belong to this branch.")
    from catalog.models import ProductVariant
    lines = clean_lines(lines)
    variants = {v.pk: v for v in ProductVariant.objects.filter(pk__in=[v for v, _ in lines], product__store=store)}
    if len(variants) != len(lines):
        raise TransferError("One of the products is not part of this branch.")
    transfer = StockTransfer.objects.create(
        reference=NumberingPrefix.next_reference(NumberingPrefix.TRANSFER), from_warehouse=from_warehouse, to_warehouse=to_warehouse,
        note=(note or "").strip()[:500], created_by=user, status=PENDING)
    for variant_id, qty in lines:
        StockTransferItem.objects.create(transfer=transfer, variant=variants[variant_id], quantity=qty)
    if mode in ("send", "complete"):
        send_transfer(transfer, user=user)
    if mode == "complete":
        receive_transfer(transfer, user=user)
    return transfer


@transaction.atomic
def send_transfer(transfer, *, user=None):
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status != PENDING:
        raise TransferError("Only a pending transfer can be sent.")
    items = list(transfer.items.select_related("variant", "variant__product").order_by("variant_id"))
    rows = {r.variant_id: r for r in StockItem.objects.select_for_update().filter(warehouse=transfer.from_warehouse, variant_id__in=[i.variant_id for i in items])}
    for item in items:                                             # check everything first, so a shortage leaves nothing half-moved
        have = rows[item.variant_id].quantity if item.variant_id in rows else Decimal("0")
        if have < item.quantity:
            raise TransferError(f"Not enough {item.variant} in {transfer.from_warehouse.name}: {_n(have)} on hand, {_n(item.quantity)} needed.")
    for item in items:
        item.unit_cost = _unit_cost(item.variant, transfer.from_warehouse)
        item.save(update_fields=["unit_cost"])
        rows[item.variant_id].adjust(-item.quantity, reference_type="TRANSFER_OUT", reference_no=transfer.reference, user=user,
                                     remarks=f"To {transfer.to_warehouse.name}")
    transfer.status, transfer.sent_at = IN_TRANSIT, timezone.now()
    transfer.save(update_fields=["status", "sent_at", "updated_at"])
    return transfer


@transaction.atomic
def receive_transfer(transfer, *, user=None):
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status != IN_TRANSIT:
        raise TransferError("Only a transfer that is in transit can be received.")
    for item in transfer.items.select_related("variant").order_by("variant_id"):
        row, _ = StockItem.objects.select_for_update().get_or_create(variant=item.variant, warehouse=transfer.to_warehouse)
        row.adjust(item.quantity, reference_type="TRANSFER_IN", reference_no=transfer.reference, user=user,
                   remarks=f"From {transfer.from_warehouse.name}", unit_cost=item.unit_cost or None)
    transfer.status, transfer.received_at = COMPLETED, timezone.now()
    transfer.save(update_fields=["status", "received_at", "updated_at"])
    return transfer


@transaction.atomic
def cancel_transfer(transfer, *, user=None):
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status == PENDING:
        pass
    elif transfer.status == IN_TRANSIT:                            # the goods are on the road: put them back on the source shelf
        for item in transfer.items.select_related("variant").order_by("variant_id"):
            row, _ = StockItem.objects.select_for_update().get_or_create(variant=item.variant, warehouse=transfer.from_warehouse)
            row.adjust(item.quantity, reference_type="TRANSFER_CANCEL", reference_no=transfer.reference, user=user,
                       remarks="Transfer cancelled", unit_cost=item.unit_cost or None)
    else:
        raise TransferError("A completed or cancelled transfer cannot be cancelled.")
    transfer.status = CANCELLED
    transfer.save(update_fields=["status", "updated_at"])
    return transfer
