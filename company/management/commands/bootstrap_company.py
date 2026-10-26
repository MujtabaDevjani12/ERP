"""
manage.py bootstrap_company [--name "My Company"]

Explicit, re-runnable backfill: creates ONE default Company (if none
exists) and links every existing Store that has no company yet. It never
changes a Store that already has a company, and never deletes anything.
Safe for a database that already has real POS data.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from company.models import Company
from tenants.models import Store


class Command(BaseCommand):
    help = "Create a default Company and attach existing branches (Stores) that have none."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="", help="Name for the default company (only used if one must be created).")

    def handle(self, *args, **options):
        orphan_stores = Store.objects.filter(company__isnull=True)
        if not orphan_stores.exists():
            self.stdout.write("Nothing to do: every branch already has a company.")
            return

        with transaction.atomic():
            company = Company.objects.order_by("id").first()
            if company is None:
                first_store = orphan_stores.order_by("id").first()
                name = options["name"] or first_store.name or "Default Company"
                company = Company.objects.create(
                    name=name,
                    currency_code=first_store.currency_code or "PKR",
                    timezone=first_store.timezone or "Asia/Karachi",
                    phone=first_store.phone,
                    email=first_store.email,
                    address=first_store.address,
                )
                self.stdout.write(f"Created company: {company.name}")
            count = orphan_stores.update(company=company)
            from accounting.management.commands.seed_chart_of_accounts import seed_chart
            seed_chart(company)                      # without a chart of accounts nothing the POS or ERP sells or buys can be posted

        self.stdout.write(self.style.SUCCESS(f"Linked {count} branch(es) to company '{company.name}'."))
