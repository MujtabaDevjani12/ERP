"""Master-data helpers: tax maths, codes, profile creation."""
from decimal import Decimal

from django.db import transaction

from core.money import D, q2
from numbering.services import next_reference
from .models import CustomerProfile, ItemProfile, VendorProfile


def compute_tax(amount, rate_percent, *, inclusive=False):
    """Return (net, tax, gross). Exclusive: tax is added on top. Inclusive:
    `amount` already contains the tax."""
    amount, rate = D(amount), D(rate_percent)
    if rate == 0:
        return q2(amount), q2(0), q2(amount)
    if inclusive:
        net = amount / (1 + rate / 100)
        return q2(net), q2(amount - q2(net)), q2(amount)
    tax = amount * rate / 100
    return q2(amount), q2(tax), q2(amount + q2(tax))


@transaction.atomic
def ensure_vendor_profile(supplier, company, **fields):
    profile = VendorProfile.objects.filter(supplier=supplier).first()
    if profile:
        return profile
    code = next_reference(company, "vendor")
    return VendorProfile.objects.create(supplier=supplier, company=company, vendor_code=code, **fields)


@transaction.atomic
def ensure_customer_profile(customer, company, **fields):
    profile = CustomerProfile.objects.filter(customer=customer).first()
    if profile:
        return profile
    code = next_reference(company, "customer")
    return CustomerProfile.objects.create(customer=customer, company=company, customer_code=code, **fields)


def ensure_item_profile(variant, **fields):
    profile, _ = ItemProfile.objects.get_or_create(variant=variant, defaults=fields)
    return profile


def item_profile_of(variant):
    """The variant's ERP profile or None (POS-only items have none)."""
    return getattr(variant, "erp_profile", None) if ItemProfile.objects.filter(variant=variant).exists() else None
