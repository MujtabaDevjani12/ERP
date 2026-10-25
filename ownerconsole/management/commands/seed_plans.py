from decimal import Decimal

from django.core.management.base import BaseCommand

from ownerconsole.models import Plan, PlatformSettings

PLANS = [
    dict(name="Starter", slug="starter", tagline="One shop, one counter", sort_order=1,
         price_monthly=Decimal("2500"), price_yearly=Decimal("25000"),
         modules=["pos", "catalog", "inventory"], max_users=3, max_stores=1, max_products=500, max_monthly_sales=1000,
         features="POS billing and receipts\nProducts, barcodes and stock\nEmail support"),
    dict(name="Business", slug="business", tagline="Growing shops with a back office", sort_order=2, is_featured=True,
         price_monthly=Decimal("7500"), price_yearly=Decimal("75000"),
         modules=["pos", "catalog", "inventory", "purchasing", "accounting", "reports"], max_users=10, max_stores=3,
         max_products=5000, max_monthly_sales=10000,
         features="Everything in Starter\nPurchasing and supplier payments\nAccounting and reports\nPriority support"),
    dict(name="Enterprise", slug="enterprise", tagline="The full ERP, no limits", sort_order=3,
         price_monthly=Decimal("20000"), price_yearly=Decimal("200000"),
         modules=["pos", "catalog", "inventory", "purchasing", "distribution", "accounting", "hr", "payroll", "production", "quality", "reports"],
         max_users=0, max_stores=0, max_products=0, max_monthly_sales=0,
         features="All modules including HR, payroll and production\nUnlimited users, branches and records\nDedicated support"),
]


class Command(BaseCommand):
    help = "Creates the three starter plans (Starter, Business, Enterprise) in PKR. Existing plans with the same slug are left alone."

    def handle(self, *args, **options):
        PlatformSettings.get()
        for data in PLANS:
            plan, created = Plan.objects.get_or_create(slug=data["slug"], defaults=data)
            self.stdout.write(f"{'Created' if created else 'Kept   '} {plan.name}")
        self.stdout.write(self.style.SUCCESS("Plans ready. Change prices and limits any time in the owner console > Plans."))
