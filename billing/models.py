"""
ERP side of the subscription system (the data of plans, subscriptions, invoices and payments lives in the separate owner database,
see the ownerconsole app).

GoogleIdentity   links a Google account to an ERP user so "Sign in with Google" works
BillingAccess    no table: only carries the permissions that say who in a company may see / manage Billing
                 (billing.view_subscription = open Billing, billing.change_subscription = change plan / cancel,
                  billing.add_payment = pay an invoice) - the "Company Admin" role has all three
"""
from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class GoogleIdentity(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="google_identity")
    sub = models.CharField(max_length=64, unique=True)
    email = models.EmailField(blank=True)


class BillingAccess(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("view_subscription", "Can open Billing (plan, usage, invoices)"),
            ("change_subscription", "Can change or cancel the plan"),
            ("add_payment", "Can pay invoices"),
            ("view_invoice", "Can view and print invoices"),
            ("view_payment", "Can see payments"),
            ("view_billingevent", "Can see billing activity"),
        ]
