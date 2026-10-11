"""
Notifications: an in-app inbox, de-duplicated per person, optional e-mail.
Alerts are produced by `generate_alerts(company)` (run daily by the `generate_notifications` command)
and by `notify_users` which other code may call directly (for example "your approval is needed").
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)


def users_with_perm(company, perm):
    """Active users of this company (or head-office users with no branch) who hold `perm`."""
    User = get_user_model()
    out = []
    for u in User.objects.filter(is_active=True).select_related("store"):
        if u.store_id and getattr(u.store, "company_id", None) not in (None, company.id):
            continue
        if u.is_superuser or u.has_perm(perm):
            out.append(u)
    return out


def notify_users(company, users, title, *, body="", level=Notification.INFO, link="", key):
    """Create one notification per user; returns how many were new. Re-sending the same `key` is a no-op."""
    created = 0
    for user in users:
        obj, was_new = Notification.objects.get_or_create(
            user=user, key=key[:120], defaults={"company": company, "title": title[:160], "body": body, "level": level, "link": link})
        if was_new:
            created += 1
            _maybe_email(obj, user)
    return created


def _maybe_email(notification, user):
    if not getattr(settings, "NOTIFY_BY_EMAIL", False) or not user.email:
        return
    try:
        send_mail(f"[ERP] {notification.title}", notification.body or notification.title, None, [user.email], fail_silently=False)
        Notification.objects.filter(pk=notification.pk).update(emailed=True)
    except Exception:                                    # noqa: BLE001 - e-mail trouble must never break business code
        logger.warning("Could not e-mail notification %s", notification.pk, exc_info=True)


def unread_count(user):
    return Notification.objects.filter(user=user, is_read=False).count()


def mark_read(user, pk=None):
    qs = Notification.objects.filter(user=user, is_read=False)
    if pk is not None:
        qs = qs.filter(pk=pk)
    return qs.update(is_read=True)


# ------------------------------------------------------------------ alert generators
def generate_alerts(company, today=None):
    """Create today's alerts for the right people. Returns {alert_name: new_notifications}."""
    today = today or timezone.localdate()
    stamp = today.isoformat()
    result = {}

    def run(name, perm, func):
        try:
            found = func()
        except Exception:                                # noqa: BLE001 - a broken module must not stop the other alerts
            logger.warning("Alert '%s' failed", name, exc_info=True)
            result[name] = 0
            return
        if not found:
            result[name] = 0
            return
        title, body, level, link = found
        result[name] = notify_users(company, users_with_perm(company, perm), title, body=body, level=level, link=link, key=f"{name}:{stamp}")

    def low_stock():
        from stock.services import reorder_alerts
        items = reorder_alerts(company)
        if items:
            names = ", ".join(str(i["variant"]) for i in items[:8])
            return (f"{len(items)} item(s) at or below reorder level", names, Notification.WARNING, "/erp/report/reorder/")

    def expiring():
        from stock.services import expiring_batches
        n = expiring_batches(company).count()
        if n:
            return (f"{n} batch(es) expire within 30 days", "", Notification.WARNING, "/erp/report/expiring/")

    def overdue_receivables():
        from distribution.services import ar_aging
        data = ar_aging(company, as_of=today)
        late = data["summary"]["61-90"] + data["summary"]["90+"]
        if late > 0:
            return (f"Customer payments over 60 days late: {late}", "", Notification.WARNING, "/erp/report/ar_aging/")

    def overdue_payables():
        from procurement.services import ap_aging
        data = ap_aging(company, as_of=today)
        late = data["summary"]["31-60"] + data["summary"]["61-90"] + data["summary"]["90+"]
        if late > 0:
            return (f"Supplier invoices over 30 days past due: {late}", "", Notification.INFO, "/erp/report/ap_aging/")

    def delayed_orders():
        from production.reports import delayed_work_orders
        n = delayed_work_orders(company, today).count()
        if n:
            return (f"{n} work order(s) are past their due date", "", Notification.CRITICAL, "/admin/production/workorder/")

    def pos_failures():
        from pos_erp.models import PosPostingIssue
        n = PosPostingIssue.objects.filter(company=company, resolved=False).count()
        if n:
            return (f"{n} POS document(s) could not be posted to accounting", "Fix the cause, then retry from the admin.", Notification.CRITICAL,
                    "/admin/pos_erp/pospostingissue/")

    run("low_stock", "stock.view_stockledgerentry", low_stock)
    run("expiring", "stock.view_stockledgerentry", expiring)
    run("ar_late", "distribution.view_salesinvoice", overdue_receivables)
    run("ap_late", "procurement.view_purchaseinvoice", overdue_payables)
    run("wo_delayed", "production.view_workorder", delayed_orders)
    run("pos_issues", "pos_erp.view_pospostingissue", pos_failures)
    approvals = notify_pending_approvals(company, today)
    result["approvals"] = approvals
    return result


def notify_pending_approvals(company, today=None):
    """One notification per approver per pending request (never repeated)."""
    from approvals.models import ApprovalRequest
    from approvals.services import can_act
    count = 0
    pending = ApprovalRequest.objects.filter(company=company, status=ApprovalRequest.PENDING)
    if not pending.exists():
        return 0
    for user in get_user_model().objects.filter(is_active=True):
        for req in pending:
            if can_act(user, req):
                count += notify_users(company, [user], f"Approval needed: {req}", level=Notification.INFO, link="/admin/approvals/approvalrequest/",
                                      key=f"approval:{req.pk}")
    return count
