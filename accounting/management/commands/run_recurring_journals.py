"""manage.py run_recurring_journals [--company NAME] [--as-of YYYY-MM-DD]
Posts the vouchers of every recurring journal that has come due. Safe to run daily from cron / Task Scheduler."""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from accounting import periods
from company.models import Company


class Command(BaseCommand):
    help = "Post all due recurring journals."

    def add_arguments(self, parser):
        parser.add_argument("--company", help="Company name (default: every company)")
        parser.add_argument("--as-of", help="Post everything due up to this date (default: today)")

    def handle(self, *args, **opts):
        companies = Company.objects.all()
        if opts["company"]:
            companies = companies.filter(name=opts["company"])
            if not companies.exists():
                raise CommandError(f'No company named "{opts["company"]}".')
        as_of = date.fromisoformat(opts["as_of"]) if opts.get("as_of") else None
        total = 0
        for company in companies:
            for template, result in periods.generate_all_due(company, as_of=as_of, user=None):
                if isinstance(result, Exception):
                    self.stderr.write(f"{company} / {template}: {result}")
                else:
                    total += len(result)
                    self.stdout.write(f"{company} / {template}: {len(result)} voucher(s)")
        self.stdout.write(self.style.SUCCESS(f"Done - {total} voucher(s) posted."))
