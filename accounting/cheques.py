"""Cheque register: issue / receive, clear (posts the voucher), bounce, cancel."""
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action
from core.errors import ERPError
from rbac.permissions import require
from .models import Cheque, JournalEntry
from .services import create_journal_entry


def _user(user):
    return user if (user is not None and getattr(user, "is_authenticated", False)) else None


@transaction.atomic
def clear_cheque(cheque: Cheque, *, on=None, user=None) -> Cheque:
    """The bank honoured the cheque: post the voucher and mark it cleared.
    Issued: Dr counter account / Cr bank.  Received: Dr bank / Cr counter account."""
    require(user, "post_document")
    cheque = Cheque.objects.select_for_update().select_related("bank_account__ledger_account", "counter_account").get(pk=cheque.pk)
    if cheque.status != Cheque.PENDING:
        raise ERPError(f"Cheque {cheque.cheque_number} is {cheque.get_status_display().lower()}; only a pending cheque can be cleared.")
    on = on or timezone.localdate()
    bank = cheque.bank_account.ledger_account
    if cheque.cheque_type == Cheque.ISSUED:
        voucher, lines = JournalEntry.PV, [{"account": cheque.counter_account, "debit": cheque.amount},
                                           {"account": bank, "credit": cheque.amount}]
    else:
        voucher, lines = JournalEntry.RV, [{"account": bank, "debit": cheque.amount},
                                           {"account": cheque.counter_account, "credit": cheque.amount}]
    for l in lines:
        l["description"] = f"Cheque {cheque.cheque_number} - {cheque.party_name}"[:255]
    entry = create_journal_entry(
        cheque.company, lines=lines, voucher_type=voucher, date=on, source=("cheque", cheque.pk), user=user,
        narration=(cheque.narration or f"Cheque {cheque.cheque_number} {cheque.party_name}")[:255],
    )
    cheque.status, cheque.cleared_on, cheque.journal_entry = Cheque.CLEARED, on, entry
    cheque.save(update_fields=["status", "cleared_on", "journal_entry", "updated_at"])
    log_action(AuditLog.POSTED, cheque, user=user, document_number=cheque.cheque_number, company=cheque.company)
    return cheque


@transaction.atomic
def bounce_cheque(cheque: Cheque, user=None) -> Cheque:
    """The bank returned it. Nothing was posted for a pending cheque, so there is nothing to reverse."""
    cheque = Cheque.objects.select_for_update().get(pk=cheque.pk)
    if cheque.status != Cheque.PENDING:
        raise ERPError("Only a pending cheque can be marked as bounced.")
    cheque.status = Cheque.BOUNCED
    cheque.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.OTHER, cheque, user=user, note="cheque bounced", document_number=cheque.cheque_number, company=cheque.company)
    return cheque


@transaction.atomic
def cancel_cheque(cheque: Cheque, user=None) -> Cheque:
    cheque = Cheque.objects.select_for_update().get(pk=cheque.pk)
    if cheque.status != Cheque.PENDING:
        raise ERPError("Only a pending cheque can be cancelled.")
    cheque.status = Cheque.CANCELLED
    cheque.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.OTHER, cheque, user=user, note="cheque cancelled", document_number=cheque.cheque_number, company=cheque.company)
    return cheque
