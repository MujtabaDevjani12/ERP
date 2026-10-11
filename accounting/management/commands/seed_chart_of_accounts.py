"""
manage.py seed_chart_of_accounts [--company "Name"]

Creates the default chart of accounts (with the system keys ERP automation
needs). Re-runnable: existing accounts (matched by code) are left alone and
only missing ones are added.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounting.defaults import DEFAULT_ACCOUNTS
from accounting.models import Account
from company.models import Company


def seed_chart(company) -> int:
    created = 0
    by_code = {a.code: a for a in Account.objects.filter(company=company)}
    with transaction.atomic():
        for code, name, acc_type, key, is_group, parent_code in DEFAULT_ACCOUNTS:
            if code in by_code:
                continue
            # a system key already used by another code is skipped (never duplicate keys)
            if key and Account.objects.filter(company=company, system_key=key).exists():
                continue
            account = Account.objects.create(
                company=company, code=code, name=name, account_type=acc_type, system_key=key,
                is_group=is_group, parent=by_code.get(parent_code) if parent_code else None,
            )
            by_code[code] = account
            created += 1
    return created


class Command(BaseCommand):
    help = "Create the default chart of accounts for a company."

    def add_arguments(self, parser):
        parser.add_argument("--company", default="", help="Company name (default: the only/first company).")

    def handle(self, *args, **options):
        qs = Company.objects.all()
        if options["company"]:
            qs = qs.filter(name=options["company"])
        company = qs.order_by("id").first()
        if company is None:
            raise CommandError("No company found. Run: python manage.py bootstrap_company")
        created = seed_chart(company)
        self.stdout.write(self.style.SUCCESS(f"{created} account(s) created for {company.name}."))
