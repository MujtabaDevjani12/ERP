"""
manage.py backfill_stock_ledger [--apply]

For every existing inventory.StockItem whose ledger total differs from its
quantity, add an OPENING_BALANCE ledger entry for the difference (valued at
the item's cost price). The POS quantity itself is NOT touched. Dry run by
default. No journal entry is posted: post opening inventory value through
Opening stock import / an opening journal.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from inventory.models import StockItem
from stock.models import ItemWarehouseCost, StockLedgerEntry
from stock.services import post_stock_movement


class Command(BaseCommand):
    help = "Create opening ledger entries so the stock ledger matches current POS stock."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        fixed = 0
        for si in StockItem.objects.select_related("variant", "warehouse"):
            ledger = StockLedgerEntry.objects.filter(variant=si.variant, warehouse=si.warehouse).aggregate(t=Sum("quantity"))["t"] or 0
            diff = si.quantity - ledger
            if diff == 0:
                continue
            fixed += 1
            self.stdout.write(f"{si.variant.sku} @ {si.warehouse.name}: stock {si.quantity}, ledger {ledger}, diff {diff}")
            if opts["apply"]:
                with transaction.atomic():
                    # write the ledger + cost rows only; StockItem already holds the right number
                    cost = ItemWarehouseCost.objects.filter(variant=si.variant, warehouse=si.warehouse).first()
                    unit_cost = cost.avg_cost if cost and cost.avg_cost > 0 else si.variant.cost_price
                    post_stock_movement(si.variant, si.warehouse, diff, reference_type="OPENING_BALANCE",
                                        reference_no="BACKFILL", unit_cost=unit_cost, allow_negative=True,
                                        remarks="Backfill from existing POS stock", _touch_legacy=False)
        self.stdout.write(self.style.SUCCESS(
            f"{fixed} row(s) {'fixed' if opts['apply'] else 'would be fixed (dry run, use --apply)'}."))
