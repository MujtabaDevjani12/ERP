"""Adds the 'your plan ends soon / payment due' banner to every page, and the Google / sign-up switches to the sign-in pages."""
from django.utils import timezone

from . import access

AUTH_PAGES = ("/accounts/login", "/signup")


def billing_shell(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        if request.path.startswith(AUTH_PAGES):               # the sign-in / sign-up pages offer Google and the Sign in | Sign up tabs
            s = access.platform_settings()
            return {"google_client_id": s.google_client_id, "signup_open": s.allow_public_signup, "business_name": s.business_name}
        return {}
    sub = getattr(request, "subscription", None)
    out = {"billing_banner": None}
    s = access.platform_settings()
    if s.announcement_enabled and s.announcement_text.strip():       # the owner's message to every company (Owner console > Settings)
        out["billing_announcement"] = {"text": s.announcement_text.strip(), "level": s.announcement_level}
    if sub is None or access.is_platform_owner(user):
        return out
    state = getattr(request, "subscription_state", None) or sub.state()
    days = sub.days_left()
    banner = None
    if state == "grace":
        banner = {"level": "danger", "text": "Your payment is overdue. Pay now to avoid your account being locked."}
    elif state == "trial" and days is not None and days <= 7:
        banner = {"level": "warning", "text": f"Your free demo ends in {max(days, 0)} day{'s' if days != 1 else ''}. Choose a plan to keep going."}
    elif state == "active" and days is not None and days <= 7:
        what = "will not renew and ends" if sub.cancel_at_period_end else "renews"
        banner = {"level": "warning" if sub.cancel_at_period_end else "info", "text": f"Your {sub.plan.name} plan {what} in {max(days, 0)} day{'s' if days != 1 else ''}."}
    if banner:
        banner["can_pay"] = user.has_perm("billing.view_subscription")
    out["billing_banner"] = banner
    out["billing_plan"] = _plan_card(request, user, sub, state, days)
    return out


TONES = {"pending": "warning", "trial": "info", "active": "success", "grace": "warning", "expired": "danger", "suspended": "danger"}
LABELS = {"pending": "Awaiting payment", "trial": "Free demo", "active": "Active", "grace": "Payment due", "expired": "Expired", "suspended": "Suspended"}


def _plan_card(request, user, sub, state, days):
    """What the sidebar plan card and the account menu show (like the plan / usage box in Claude): plan, status, time left,
    the plan limits that matter and an Upgrade button. Only people who may open Billing get the numbers."""
    can_view = user.has_perm("billing.view_subscription")
    now = timezone.now()
    start = sub.current_period_start if sub.current_period_end else sub.started_at
    end = sub.paid_until
    pct = 0
    if end and start and end > start:
        pct = max(0, min(100, int((now - start).total_seconds() * 100 / (end - start).total_seconds())))
    if state in ("pending", "expired", "suspended"):
        pct = 100 if state != "pending" else 0
    if state == "pending":
        line = "Pay your first invoice to start"
    elif state == "suspended":
        line = "Contact support"
    elif state == "expired":
        line = "Pay to unlock your account"
    elif days is None:
        line = ""
    elif state == "grace":
        line = "Payment overdue"
    else:
        what = "Demo ends" if state == "trial" else ("Ends" if sub.cancel_at_period_end else "Renews")
        line = f"{what} in {max(days, 0)} day{'s' if days != 1 else ''}" if days <= 30 else f"{what} {end:%d %b %Y}"
    usage = []
    if can_view and state in ("trial", "active", "grace"):
        try:
            usage = [r for r in access.limit_rows(getattr(request, "company", None)) if not r["unlimited"]][:3]
        except Exception:                                    # noqa: BLE001 - the card is decoration, it must never break a page
            usage = []
    return {"name": sub.plan.name, "state": state, "label": LABELS.get(state, state), "tone": TONES.get(state, "neutral"), "line": line,
            "pct": pct, "usage": usage, "can_view": can_view, "can_upgrade": user.has_perm("billing.change_subscription"),
            "interval": sub.get_interval_display(), "locked": state in ("pending", "expired", "suspended")}
