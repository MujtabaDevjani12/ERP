"""Re-post POS documents that earlier failed or were never posted."""
from django.db import transaction

from . import services as s
from .models import PosPostingIssue


def _post(doc_type, doc_id, user=None):
    from payments.models import Payment
    from sales.models import Sale
    if doc_type == "sale":
        s.post_pos_sale(Sale.objects.get(pk=doc_id), user)
    elif doc_type == "payment":
        payment = Payment.objects.select_related("sale").get(pk=doc_id)
        s.post_pos_sale(payment.sale, user)
        s.post_pos_payment(payment, user)
    elif doc_type == "return":
        from .models import PosReturn
        s.post_pos_return(PosReturn.objects.get(sale_return_id=doc_id), user)
    else:
        raise ValueError(f"Cannot retry a '{doc_type}' document automatically.")


def retry_issue(issue: PosPostingIssue, user=None) -> bool:
    try:
        with transaction.atomic():
            _post(issue.doc_type, issue.doc_id, user)
    except Exception as exc:                          # noqa: BLE001
        issue.error = str(exc)[:2000]
        issue.save(update_fields=["error", "updated_at"])
        return False
    issue.resolved = True
    issue.save(update_fields=["resolved", "updated_at"])
    return True


def backfill(company, *, apply=False, since=None):
    """Post every completed sale / payment / return that has no journal yet. Returns counts (dry-run unless apply)."""
    from accounting.models import JournalEntry
    from payments.models import Payment
    from sales.models import Sale

    sales = Sale.objects.filter(store__company=company, status__in=["completed", "partially_returned", "returned"])
    if since:
        sales = sales.filter(created_at__date__gte=since)
    done = set(JournalEntry.objects.filter(company=company, source_type="pos_sale").values_list("source_id", flat=True))
    pending_sales = [x for x in sales if str(x.pk) not in done]
    pay_done = set(JournalEntry.objects.filter(company=company, source_type="pos_payment").values_list("source_id", flat=True))
    pending_pay = [p for p in Payment.objects.filter(sale__in=sales).select_related("sale") if str(p.pk) not in pay_done]
    from .models import PosReturn
    ret_done = set(JournalEntry.objects.filter(company=company, source_type="pos_return").values_list("source_id", flat=True))
    pending_returns = [r for r in PosReturn.objects.filter(sale_return__sale__in=sales).select_related("sale_return__sale")
                       if str(r.pk) not in ret_done]
    result = {"sales": len(pending_sales), "payments": len(pending_pay), "returns": len(pending_returns), "failed": 0}
    if not apply:
        return result
    for sale in pending_sales:
        try:
            with transaction.atomic():
                s.post_pos_sale(sale)
        except Exception as exc:                      # noqa: BLE001
            s._record_issue(company, "sale", sale, exc)
            result["failed"] += 1
    for p in pending_pay:
        try:
            with transaction.atomic():
                s.post_pos_payment(p)
        except Exception as exc:                      # noqa: BLE001
            s._record_issue(company, "payment", p, exc)
            result["failed"] += 1
    for r in pending_returns:                         # returns last: they reverse part of the sale + payments posted above
        try:
            with transaction.atomic():
                s.post_pos_return(r)
        except Exception as exc:                      # noqa: BLE001
            s._record_issue(company, "return", r.sale_return, exc)
            result["failed"] += 1
    return result
