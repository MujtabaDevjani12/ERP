from django.core.management.base import BaseCommand

from company.models import Company
from notifications.services import generate_alerts


class Command(BaseCommand):
    help = "Create today's alerts (low stock, expiring batches, late receivables/payables, delayed work orders, approvals). Safe to run many times a day."

    def add_arguments(self, parser):
        parser.add_argument("--company", help="Company name (default: all companies)")

    def handle(self, *args, company=None, **opts):
        qs = Company.objects.filter(name=company) if company else Company.objects.all()
        for comp in qs:
            self.stdout.write(f"{comp}: {generate_alerts(comp)}")
