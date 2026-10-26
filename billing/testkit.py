"""Helpers shared by the billing and owner-console tests (no test cases live here)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from billing import services
from core.pos_fixtures import PASSWORD
from ownerconsole import services as owner
from ownerconsole.models import OwnerAccount, Plan

User = get_user_model()
OWNER_PASSWORD = "Sup3r-secret-owner-pw"


def make_plans():
    starter = Plan.objects.create(name="Starter", slug="starter", price_monthly=Decimal("2500"), price_yearly=Decimal("25000"),
                                  modules=["pos", "catalog"], max_users=2, max_stores=1, max_products=3, max_monthly_sales=2,
                                  features="Email support", sort_order=1)
    pro = Plan.objects.create(name="Pro", slug="pro", price_monthly=Decimal("7500"), price_yearly=Decimal("75000"),
                              modules=["pos", "catalog", "inventory", "purchasing", "accounting", "hr", "reports"],
                              max_users=0, max_stores=0, max_products=0, max_monthly_sales=0, is_featured=True, sort_order=2)
    return starter, pro


def signup(name, email, plan, interval="monthly"):
    """Creates a company the way the sign-up page does. Returns (company, user, subscription); the subscription is PENDING."""
    return services.create_company_with_owner(company_name=name, owner_name=f"{name} Owner", email=email, password=PASSWORD,
                                              plan=plan, interval=interval)


def pay(sub, by=None):
    """Settle the open invoice of a subscription (as if the owner approved the payment)."""
    inv = owner.mark_invoice_paid(owner.open_invoice(sub), by=by)
    sub.refresh_from_db()
    return inv


def last_code():
    """The 6-digit verification code in the newest e-mail the test outbox holds."""
    import re
    from django.core import mail
    return re.match(r"(\d{6})", mail.outbox[-1].subject).group(1)


def make_owner(email="boss@owner.test", role=OwnerAccount.ADMIN, name="Boss", password=OWNER_PASSWORD):
    acct = OwnerAccount(email=email, name=name, role=role)
    acct.set_password(password)
    acct.save()
    return acct


class BillingBase(TestCase):
    """Roles, two plans and an ERP Super Admin with no branch."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_roles", verbosity=0)
        cls.starter, cls.pro = make_plans()
        cls.erp_admin = User.objects.create_superuser("boss", "boss@example.com", PASSWORD)
