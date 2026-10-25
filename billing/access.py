"""What a company's plan allows and how much of each limit it has used. Reads the owner database; never writes to it."""
import logging

from django.db import DatabaseError
from django.utils import timezone

from .modules import module_for_app

logger = logging.getLogger(__name__)


def is_platform_owner(user):
    """True for a Super Admin of this installation (the person who runs the ERP itself).

    They are never limited by a plan and can see every company. The people who SELL the ERP use the separate owner console instead.
    """
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False))


def company_of(request_or_user):
    """The Company a request / user belongs to (None for a Super Admin without a branch)."""
    company = getattr(request_or_user, "company", None)
    if company is not None:
        return company
    store = getattr(request_or_user, "store", None)
    return getattr(store, "company", None)


def platform_settings():
    """The owner's platform settings, read-only. Safe before the owner database exists (returns defaults)."""
    from ownerconsole.models import PlatformSettings
    try:
        return PlatformSettings.peek()
    except DatabaseError:
        logger.warning("Owner database is not ready (run: python manage.py setup_owner)")
        return PlatformSettings(pk=1)


def subscription_of(company):
    """The company's Subscription from the owner database, or None.

    None also means "billing is not set up here" (older single-company install, or the owner database does not exist yet):
    such a company is never restricted.
    """
    if company is None:
        return None
    from ownerconsole.models import Subscription
    try:
        return Subscription.objects.select_related("plan").filter(company_id=company.pk).first()
    except DatabaseError:
        logger.error("Owner database unreachable - billing limits are NOT enforced (run: python manage.py setup_owner)", exc_info=True)
        return None


def managed(company):
    """True when the company has a subscription. Companies without one are never restricted."""
    return subscription_of(company) is not None


def allowed_modules(company):
    """None = everything (no subscription), otherwise the set of module keys of the plan."""
    sub = subscription_of(company)
    if sub is None:
        return None
    return set(sub.plan.module_keys)


def module_allowed(company, app_label):
    module = module_for_app(app_label)
    if module is None:
        return True
    mods = allowed_modules(company)
    return mods is None or module in mods


# ---------------------------------------------------------------- limits
LIMIT_LABELS = {"users": "users", "stores": "branches", "products": "products", "monthly_sales": "sales / invoices this month"}


def usage(company):
    from accounts.models import User
    from catalog.models import Product
    from sales.models import Sale
    from tenants.models import Store
    start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "users": User.objects.filter(store__company=company, is_active=True).count(),
        "stores": Store.objects.filter(company=company, is_active=True).count(),
        "products": Product.objects.filter(store__company=company).count(),
        "monthly_sales": Sale.objects.filter(store__company=company, created_at__gte=start).count(),
    }


def rows_from(sub, used):
    """[{key,label,used,limit,percent,unlimited,reached}] for a subscription and a usage dict."""
    rows = []
    for key, label in LIMIT_LABELS.items():
        limit = sub.limit(key)
        unlimited = not limit
        rows.append({"key": key, "label": label.capitalize(), "used": used[key], "limit": limit, "unlimited": unlimited,
                     "percent": 0 if unlimited else min(100, int(used[key] * 100 / limit)),
                     "reached": (not unlimited) and used[key] >= limit})
    return rows


def limit_rows(company):
    """Rows for the Billing page; empty when the company has no subscription."""
    sub = subscription_of(company)
    if sub is None:
        return []
    return rows_from(sub, usage(company))


def limit_reached(company, key):
    """(True, message) when the company is at its plan limit for `key`; (False, "") otherwise."""
    sub = subscription_of(company)
    if sub is None:
        return False, ""
    limit = sub.limit(key)
    if not limit:
        return False, ""
    used = usage(company)[key]
    if used >= limit:
        return True, f"Your {sub.plan.name} plan allows {limit:,} {LIMIT_LABELS[key]}. Upgrade your plan from Billing to add more."
    return False, ""
