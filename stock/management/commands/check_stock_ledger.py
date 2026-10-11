"""manage.py check_stock_ledger — reports any StockItem whose quantity differs from its ledger total."""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum

from inventory.models import StockItem
from stock.models import StockLedgerEntry


class Command(BaseCommand):
    help = "Compare POS stock quantities with the stock ledger."

    def handle(self, *args, **opts):
        bad = 0
        for si in StockItem.objects.select_related("variant", "warehouse"):
            ledger = StockLedgerEntry.objects.filter(variant=si.variant, warehouse=si.warehouse).aggregate(t=Sum("quantity"))["t"] or 0
            if ledger != si.quantity:
                bad += 1
                self.stdout.write(self.style.WARNING(f"{si.variant.sku} @ {si.warehouse.name}: stock {si.quantity} != ledger {ledger}"))
        if bad:
            raise CommandError(f"{bad} mismatch(es). Run: python manage.py backfill_stock_ledger --apply")
        self.stdout.write(self.style.SUCCESS("Stock and ledger agree."))
