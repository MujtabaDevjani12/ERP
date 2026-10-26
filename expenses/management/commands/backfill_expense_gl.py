"""
manage.py backfill_expense_gl [--apply]

Posts the journal entry for every saved expense that has none yet (Dr expense account / Cr Cash or Bank). Dry run by default; safe to repeat.
"""
from django.core.management.base import BaseCommand

from company.utils import company_of_store
from expenses.accounting import _entries, sync_expense
from expenses.models import Expense


class Command(BaseCommand):
    help = "Post missing journal entries for saved expenses."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **opts):
        todo = posted = failed = 0
        for e in Expense.objects.select_related("store", "category"):
            company = company_of_store(e.store)
            if company is None or _entries(company, e).exists():
                continue
            todo += 1
            if opts["apply"]:
                ok = sync_expense(e)
                posted, failed = posted + bool(ok), failed + (not ok)
        if opts["apply"]:
            self.stdout.write(self.style.SUCCESS(f"Posted {posted} expense entries; {failed} could not be posted (closed period or no chart of accounts?)."))
        else:
            self.stdout.write(f"{todo} expenses have no journal entry. Run again with --apply to post them.")
