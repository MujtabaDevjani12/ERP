"""
manage.py backfill_stock_gl [--apply]

Posts the missing accounting entries for stock that was added before they were posted automatically:
  * opening stock typed on the product form          Dr Inventory / Cr Opening balance equity
  * received purchases from Suppliers > Purchases    Dr Inventory / Cr Accounts payable (+ their payments and returns)
Entries that already exist are skipped, so the command is safe to run again. Dry run by default.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounting.models import JournalEntry
from company.utils import company_of_store
from stock import gl
from stock.models import StockLedgerEntry


class Command(BaseCommand):
    help = "Post missing journal entries for opening stock and legacy purchases."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def _exists(self, store, source_type, source_id):
        company = company_of_store(store)
        return company is None or JournalEntry.objects.filter(company=company, source_type=source_type, source_id=str(source_id)).exists()

    def handle(self, *args, **opts):
        apply = opts["apply"]
        todo = posted = failed = 0

        for e in StockLedgerEntry.objects.filter(reference_type="OPENING_STOCK", quantity__gt=0).select_related("variant", "warehouse__store"):
            sid = f"{e.variant_id}:{e.warehouse_id}:{e.reference_no}"
            if self._exists(e.warehouse.store, "opening_stock", sid):
                continue
            todo += 1
            if apply:
                ok = gl.post_opening_stock(e.warehouse, e.variant, e.quantity, e.unit_cost, reference_no=e.reference_no,
                                           date=timezone.localtime(e.posted_at).date())
                posted, failed = posted + bool(ok), failed + (not ok)

        from suppliers.models import PurchasePayment, PurchaseReturn, PurchaseOrder
        for o in PurchaseOrder.objects.filter(status="received").select_related("warehouse__store", "supplier"):
            if getattr(o, "erp", None) is not None:
                continue
            if not self._exists(o.warehouse.store, "po_legacy", o.pk):
                todo += 1
                if apply:
                    ok = gl.post_legacy_receipt(o)
                    posted, failed = posted + bool(ok), failed + (not ok)
            for p in PurchasePayment.objects.filter(purchase_order=o):
                if not self._exists(o.warehouse.store, "po_legacy_payment", p.pk):
                    todo += 1
                    if apply:
                        ok = gl.post_legacy_payment(p)
                        posted, failed = posted + bool(ok), failed + (not ok)
            for r in PurchaseReturn.objects.filter(purchase_order=o):
                if not self._exists(o.warehouse.store, "po_legacy_return", r.pk):
                    todo += 1
                    if apply:
                        ok = gl.post_legacy_return(r)
                        posted, failed = posted + bool(ok), failed + (not ok)

        if apply:
            self.stdout.write(self.style.SUCCESS(f"Posted {posted} entries; {failed} could not be posted (closed period or no chart of accounts?)."))
        else:
            self.stdout.write(f"{todo} entries are missing. Run again with --apply to post them.")
