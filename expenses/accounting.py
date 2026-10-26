"""Every expense reaches the books: Dr <expense account> / Cr Cash (or Bank).

Before this, an expense lowered the dashboard's profit but never appeared in the Profit & Loss or Trial balance. Posting is kept out of the
way of the screens (a missing chart of accounts or a locked period is logged, the expense is still saved) and `manage.py backfill_expense_gl`
posts the ones that were saved earlier. Editing an expense reverses its old entry and posts a new one; deleting reverses it.
"""
import logging

from django.db import transaction

from accounting.models import JournalEntry
from accounting.services import create_journal_entry, reverse_entry
from company.utils import company_of_store

logger = logging.getLogger(__name__)

KEYWORDS = (
    ("rent", "rent"), ("lease", "rent"),
    ("salary", "salary_expense"), ("wage", "salary_expense"), ("payroll", "salary_expense"),
    ("electric", "utilities"), ("utilit", "utilities"), ("water", "utilities"), ("gas", "utilities"), ("internet", "utilities"), ("phone", "utilities"),
    ("transport", "transport"), ("fuel", "transport"), ("petrol", "transport"), ("delivery", "transport"), ("freight", "transport"),
    ("market", "marketing"), ("advert", "marketing"), ("promo", "marketing"),
)
CASH_METHODS = {"cash"}


def expense_account_key(category_name: str) -> str:
    name = (category_name or "").lower()
    for word, key in KEYWORDS:
        if word in name:
            return key
    return "other_expense"


def _source_id(expense, version):
    return str(expense.pk) if version == 0 else f"{expense.pk}:v{version}"


def _entries(company, expense):
    return JournalEntry.objects.filter(company=company, source_type="expense", source_id__regex=rf"^{expense.pk}(:v\d+)?$").order_by("id")


def _enabled(store):
    try:
        from pos_erp.services import accounting_enabled
        return accounting_enabled(store)
    except Exception:                                                    # noqa: BLE001
        return True


def sync_expense(expense, user=None):
    """Make the books match the expense as it is now. Safe to call again and again."""
    company = company_of_store(expense.store)
    if company is None or not _enabled(expense.store):
        return None
    wanted = (expense.amount, expense_account_key(expense.category.name), "cash" if expense.method in CASH_METHODS else "bank", expense.expense_date)
    try:
        with transaction.atomic():
            live = [e for e in _entries(company, expense) if e.status == JournalEntry.POSTED]
            if live:
                cur = live[-1]
                lines = {l.account.system_key: (l.debit, l.credit) for l in cur.lines.select_related("account")}
                same = (cur.date == wanted[3] and lines.get(wanted[1], (0, 0))[0] == wanted[0] and lines.get(wanted[2], (0, 0))[1] == wanted[0])
                if same:
                    return cur
                for e in live:
                    reverse_entry(e, None, reason=f"Expense {expense.reference} changed")
            version = _entries(company, expense).count()
            return create_journal_entry(
                company, date=expense.expense_date, narration=f"Expense {expense.reference}: {expense.category.name}" + (f" - {expense.paid_to}" if expense.paid_to else ""),
                source=("expense", _source_id(expense, version)), store=expense.store, user=user, voucher_type="PV",
                lines=[{"account": wanted[1], "debit": expense.amount}, {"account": wanted[2], "credit": expense.amount}])
    except Exception as exc:                                             # noqa: BLE001 - saving the expense must not fail because of the books
        logger.warning("Expense %s could not be posted: %s", expense.pk, exc)
        return None


def void_expense(expense):
    company = company_of_store(expense.store)
    if company is None:
        return
    try:
        with transaction.atomic():
            for e in _entries(company, expense):
                if e.status == JournalEntry.POSTED:
                    reverse_entry(e, None, reason=f"Expense {expense.reference} deleted")
    except Exception as exc:                                             # noqa: BLE001
        logger.warning("Expense %s could not be reversed: %s", expense.pk, exc)
