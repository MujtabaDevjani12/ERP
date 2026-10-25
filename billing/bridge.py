"""
The ONLY place where the owner side reaches into the ERP database (companies, their admins, in-app notifications, usage).
Everything is best effort: when the ERP database cannot be read the owner console keeps working with the snapshots it stores itself.
"""
import logging

logger = logging.getLogger(__name__)


def company_obj(company_id):
    from company.models import Company
    try:
        return Company.objects.filter(pk=company_id).first()
    except Exception:                                              # noqa: BLE001
        logger.warning("Could not read company %s", company_id, exc_info=True)
        return None


def company_info(company_id):
    c = company_obj(company_id)
    if c is None:
        return None
    return {"name": c.name, "email": c.email or "", "phone": getattr(c, "phone", "") or "", "address": getattr(c, "address", "") or ""}


def admin_users(company_id):
    """Active users of the company who manage its billing (they get the reminders)."""
    from accounts.models import User
    try:
        return [u for u in User.objects.filter(is_active=True, store__company_id=company_id) if u.has_perm("billing.view_subscription")]
    except Exception:                                              # noqa: BLE001
        logger.warning("Could not list admins of company %s", company_id, exc_info=True)
        return []


def admin_emails(company_id):
    out = []
    for u in admin_users(company_id):
        if u.email and u.email.lower() not in out:
            out.append(u.email.lower())
    return out


def notify_admins(company_id, title, body="", level="info", link="/billing/", key=""):
    """In-app notification (the bell in the ERP) for the company's billing admins."""
    from notifications.services import notify_users
    company = company_obj(company_id)
    users = admin_users(company_id)
    if company is None or not users:
        return 0
    try:
        return notify_users(company, users, title, body=body, level=level, link=link, key=key[:120])
    except Exception:                                              # noqa: BLE001 - a notification problem must never block billing
        logger.warning("Billing notification failed", exc_info=True)
        return 0


def user_counts(company_ids):
    from django.db.models import Count
    from accounts.models import User
    try:
        rows = (User.objects.filter(store__company_id__in=list(company_ids), is_active=True)
                .values("store__company_id").annotate(n=Count("id")))
        return {r["store__company_id"]: r["n"] for r in rows}
    except Exception:                                              # noqa: BLE001
        return {}


def usage(company_id):
    from . import access
    company = company_obj(company_id)
    if company is None:
        return None
    try:
        return access.usage(company)
    except Exception:                                              # noqa: BLE001
        logger.warning("Could not read usage of company %s", company_id, exc_info=True)
        return None


def companies_without_subscription(known_ids):
    from company.models import Company
    try:
        return list(Company.objects.exclude(pk__in=list(known_ids)).order_by("name"))
    except Exception:                                              # noqa: BLE001
        return []
