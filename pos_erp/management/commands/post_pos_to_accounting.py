from datetime import date

from django.core.management.base import BaseCommand, CommandError

from company.models import Company
from pos_erp import retry


class Command(BaseCommand):
    help = ("Post historical POS sales/payments to accounting and retry earlier failures. "
            "Dry-run by default; add --apply to write. Safe to run repeatedly (idempotent).")

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True, help="Company name")
        parser.add_argument("--since", help="Only sales from this date (YYYY-MM-DD)")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, company, since=None, apply=False, **opts):
        try:
            comp = Company.objects.get(name=company)
        except Company.DoesNotExist:
            raise CommandError(f"No company named '{company}'.")
        since_date = date.fromisoformat(since) if since else None
        res = retry.backfill(comp, apply=apply, since=since_date)
        verb = "Posted" if apply else "Would post"
        self.stdout.write(f"{verb}: {res['sales']} sale(s), {res['payments']} payment(s), {res['returns']} return(s); failures: {res['failed']}")
        if not apply:
            self.stdout.write("Dry run only. Re-run with --apply to write journal entries.")
