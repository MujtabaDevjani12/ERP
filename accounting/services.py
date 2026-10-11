"""
Double-entry engine. EVERY financial effect in the ERP goes through
create_journal_entry(); nothing else writes JournalLine rows.

    from accounting.services import create_journal_entry

    entry = create_journal_entry(
        company, voucher_type="SYSTEM", date=today, narration="GRN-2026-00001",
        source=("goods_receipt", grn.pk),
        lines=[
            {"account": "inventory", "debit": 5000},          # system key or Account
            {"account": "grni", "credit": 5000, "party_type": "vendor", "party_id": 7},
        ],
    )

Rules enforced here:
  * debits == credits (to the paisa) or nothing is saved;
  * at least two non-zero lines; one side per line; no negatives;
  * group (non-postable) and inactive accounts are rejected; accounts must
    belong to the same company;
  * entries on/before the company's period lock date are rejected;
  * `source=(type, id)` makes posting IDEMPOTENT: calling twice for the same
    document returns the entry created the first time instead of posting twice;
  * posted entries are immutable. Use reverse_entry().
"""
from datetime import date as date_type
from typing import Iterable, Optional

from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action
from core.errors import ERPError, UnbalancedJournal
from core.money import ZERO, D, q2
from numbering.services import next_reference
from rbac.permissions import require
from .models import Account, AccountingPeriodLock, JournalEntry, JournalLine

_NUMBERING_FOR_VOUCHER = {
    JournalEntry.JV: "journal_voucher",
    JournalEntry.PV: "payment_voucher",
    JournalEntry.RV: "receipt_voucher",
    JournalEntry.CONTRA: "contra_voucher",
    JournalEntry.DEBIT_NOTE: "debit_note",
    JournalEntry.CREDIT_NOTE: "credit_note_voucher",
    JournalEntry.OPENING: "opening_voucher",
    JournalEntry.SYSTEM: "system_journal",
}


def get_account(company, system_key: str) -> Account:
    try:
        return Account.objects.get(company=company, system_key=system_key, is_active=True)
    except Account.DoesNotExist:
        raise ERPError(
            f"Account with system key '{system_key}' does not exist for {company}. "
            f"Run: python manage.py seed_chart_of_accounts --company \"{company.name}\""
        )


def _resolve_account(company, ref) -> Account:
    if isinstance(ref, Account):
        account = ref
    else:
        account = get_account(company, str(ref)) if not str(ref).isdigit() else Account.objects.get(pk=int(ref))
    if account.company_id != company.id:
        raise ERPError(f"Account {account} belongs to a different company.")
    if account.is_group:
        raise ERPError(f"Account {account} is a group account; post to a detail account.")
    if not account.is_active:
        raise ERPError(f"Account {account} is inactive.")
    return account


def _normalize_lines(company, lines: Iterable[dict]):
    clean = []
    for raw in lines:
        debit, credit = q2(raw.get("debit", 0)), q2(raw.get("credit", 0))
        if debit < 0 or credit < 0:
            raise ERPError("Debit and credit amounts cannot be negative.")
        if debit > 0 and credit > 0:
            raise ERPError("A journal line cannot have both a debit and a credit.")
        if debit == 0 and credit == 0:
            continue  # zero lines carry no information
        account = _resolve_account(company, raw["account"])
        clean.append({
            "account": account, "debit": debit, "credit": credit,
            "cost_center": raw.get("cost_center"), "description": (raw.get("description") or "")[:255],
            "party_type": raw.get("party_type", ""), "party_id": raw.get("party_id"),
        })
    return clean


def _check_balanced(lines):
    if len(lines) < 2:
        raise UnbalancedJournal("A journal entry needs at least two non-zero lines.")
    total_debit = sum((l["debit"] for l in lines), ZERO)
    total_credit = sum((l["credit"] for l in lines), ZERO)
    if total_debit != total_credit:
        raise UnbalancedJournal(
            f"Journal is not balanced: debit {total_debit} != credit {total_credit} "
            f"(difference {total_debit - total_credit})."
        )
    return total_debit, total_credit


def _check_period_open(company, entry_date):
    lock = AccountingPeriodLock.objects.filter(company=company).first()
    if lock and entry_date <= lock.locked_through:
        raise ERPError(f"The accounting period is locked through {lock.locked_through}; cannot post on {entry_date}.")


@transaction.atomic
def create_journal_entry(company, *, lines, voucher_type=JournalEntry.SYSTEM, date: Optional[date_type] = None,
                         narration="", source=None, store=None, user=None, post=True, meta=None) -> JournalEntry:
    date = date or timezone.localdate()
    source_type, source_id = ("", "")
    if source:
        source_type, source_id = str(source[0]), str(source[1])
        existing = JournalEntry.objects.filter(company=company, source_type=source_type, source_id=source_id).first()
        if existing:
            return existing

    clean = _normalize_lines(company, lines)
    total_debit, total_credit = _check_balanced(clean)
    _check_period_open(company, date)

    entry = JournalEntry.objects.create(
        company=company, store=store, voucher_type=voucher_type, date=date, narration=narration[:255],
        source_type=source_type, source_id=source_id, meta=meta or {},
        total_debit=total_debit, total_credit=total_credit,
        created_by=user if (user is not None and getattr(user, "is_authenticated", False)) else None,
    )
    for l in clean:
        JournalLine.objects.create(entry=entry, **l)
    log_action(AuditLog.CREATED, entry, user=user, new={"voucher_type": voucher_type, "debit": str(total_debit)},
               company=company, note=narration[:200])
    if post:
        _post(entry, user)
    return entry


def _post(entry: JournalEntry, user=None) -> JournalEntry:
    entry.entry_number = next_reference(
        entry.company, _NUMBERING_FOR_VOUCHER.get(entry.voucher_type, "system_journal"),
        store=entry.store, on_date=entry.date,
    )
    entry.status = JournalEntry.POSTED
    entry.posted_at = timezone.now()
    entry.posted_by = user if (user is not None and getattr(user, "is_authenticated", False)) else None
    entry.save()
    log_action(AuditLog.POSTED, entry, user=user, document_number=entry.entry_number, company=entry.company)
    return entry


@transaction.atomic
def post_entry(entry: JournalEntry, user=None) -> JournalEntry:
    """Post a DRAFT entry (e.g. one a user keyed in manually)."""
    require(user, "post_document")
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status != JournalEntry.DRAFT:
        raise ERPError("Only a draft entry can be posted.")
    lines = [
        {"account": l.account, "debit": l.debit, "credit": l.credit, "cost_center": l.cost_center,
         "description": l.description, "party_type": l.party_type, "party_id": l.party_id}
        for l in entry.lines.select_related("account")
    ]
    clean = _normalize_lines(entry.company, lines)
    total_debit, total_credit = _check_balanced(clean)
    _check_period_open(entry.company, entry.date)
    entry.total_debit, entry.total_credit = total_debit, total_credit
    return _post(entry, user)


@transaction.atomic
def reverse_entry(entry: JournalEntry, user=None, *, date: Optional[date_type] = None, reason="") -> JournalEntry:
    require(user, "reverse_document")
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status != JournalEntry.POSTED:
        raise ERPError("Only a posted, not-yet-reversed entry can be reversed.")
    if hasattr(entry, "reversed_by") and entry.reversed_by is not None:
        raise ERPError("This entry was already reversed.")

    lines = [
        {"account": l.account, "debit": l.credit, "credit": l.debit, "cost_center": l.cost_center,
         "description": f"Reversal: {l.description}"[:255], "party_type": l.party_type, "party_id": l.party_id}
        for l in entry.lines.select_related("account")
    ]
    reversal = create_journal_entry(
        entry.company, lines=lines, voucher_type=entry.voucher_type, date=date or timezone.localdate(),
        narration=f"Reversal of {entry.entry_number}. {reason}".strip()[:255],
        source=("reversal", entry.pk), store=entry.store, user=user, post=False,
    )
    reversal.reversal_of = entry
    reversal.save()                       # still draft, so allowed
    _post(reversal, user)
    JournalEntry.objects.filter(pk=entry.pk).update(status=JournalEntry.REVERSED)   # bypasses the immutability guard on purpose
    log_action(AuditLog.REVERSED, entry, user=user, document_number=entry.entry_number,
               note=reason[:200], company=entry.company)
    return reversal


def set_period_lock(company, locked_through):
    obj, _ = AccountingPeriodLock.objects.update_or_create(company=company, defaults={"locked_through": locked_through})
    return obj
