"""Fiscal-year close / reopen and recurring journals."""
import calendar
from datetime import date

from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action
from core.errors import ERPError
from rbac.permissions import require
from .models import AccountingPeriodLock, FiscalYear, RecurringJournal
from .services import create_journal_entry


# ---------------------------------------------------------------- fiscal years
@transaction.atomic
def close_fiscal_year(year: FiscalYear, user=None) -> FiscalYear:
    """Lock the books through the last day of the year. Everything dated on/before it becomes read-only."""
    require(user, "post_document")
    year = FiscalYear.objects.select_for_update().get(pk=year.pk)
    if year.is_closed:
        raise ERPError(f"{year} is already closed.")
    earlier_open = FiscalYear.objects.filter(company=year.company, is_closed=False, end_date__lt=year.start_date).first()
    if earlier_open:
        raise ERPError(f"Close {earlier_open} first - years are closed in order.")
    from .models import JournalEntry
    drafts = JournalEntry.objects.filter(company=year.company, status=JournalEntry.DRAFT, date__lte=year.end_date).count()
    if drafts:
        raise ERPError(f"{drafts} draft journal entr{'y' if drafts == 1 else 'ies'} dated in {year} must be posted or deleted before closing.")
    lock, _ = AccountingPeriodLock.objects.get_or_create(company=year.company, defaults={"locked_through": year.end_date})
    if lock.locked_through < year.end_date:
        lock.locked_through = year.end_date
        lock.save()
    year.is_closed, year.closed_on = True, timezone.now()
    year.closed_by = user if (user is not None and getattr(user, "is_authenticated", False)) else None
    year.save()
    log_action(AuditLog.OTHER, year, user=user, note=f"fiscal year closed; books locked through {year.end_date}", company=year.company)
    return year


@transaction.atomic
def reopen_fiscal_year(year: FiscalYear, user=None) -> FiscalYear:
    """Undo a close. Only the most recent closed year can be reopened; the lock falls back to the year before it."""
    require(user, "reverse_document")
    year = FiscalYear.objects.select_for_update().get(pk=year.pk)
    if not year.is_closed:
        raise ERPError(f"{year} is not closed.")
    later = FiscalYear.objects.filter(company=year.company, is_closed=True, start_date__gt=year.start_date).first()
    if later:
        raise ERPError(f"Reopen {later} first - only the latest closed year can be reopened.")
    previous = FiscalYear.objects.filter(company=year.company, is_closed=True, end_date__lt=year.start_date).order_by("-end_date").first()
    if previous:
        AccountingPeriodLock.objects.filter(company=year.company).update(locked_through=previous.end_date)
    else:
        AccountingPeriodLock.objects.filter(company=year.company).delete()
    year.is_closed, year.closed_on, year.closed_by = False, None, None
    year.save()
    log_action(AuditLog.OTHER, year, user=user, note="fiscal year reopened", company=year.company)
    return year


# ---------------------------------------------------------------- recurring journals
def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _step(template: RecurringJournal, anchor_day: int, current: date) -> date:
    """Next run date, keeping the original day of month (31st stays the last day of shorter months)."""
    nxt = add_months(current.replace(day=1), RecurringJournal.MONTHS[template.frequency])
    return nxt.replace(day=min(anchor_day, calendar.monthrange(nxt.year, nxt.month)[1]))


def generate_recurring(template: RecurringJournal, *, as_of=None, user=None, max_entries=24):
    """Post one voucher per period that is due on or before `as_of`. Returns the entries created.
    Posting is idempotent per (template, date), so running twice never double-posts."""
    require(user, "post_document")
    as_of = as_of or timezone.localdate()
    lines = list(template.lines.select_related("account", "cost_center"))
    if len(lines) < 2:
        raise ERPError(f'"{template.name}" needs at least two lines.')
    created = []
    anchor_day = template.next_run_date.day
    while (template.is_active and template.next_run_date <= as_of and (template.end_date is None or template.next_run_date <= template.end_date)
           and len(created) < max_entries):
        run_date = template.next_run_date
        entry = create_journal_entry(
            template.company, voucher_type=template.voucher_type, date=run_date,
            narration=(template.narration or template.name)[:255], source=("recurring_journal", f"{template.pk}:{run_date.isoformat()}"), user=user,
            lines=[{"account": l.account, "debit": l.debit, "credit": l.credit, "cost_center": l.cost_center, "description": l.description}
                   for l in lines],
        )
        created.append(entry)
        template.last_run_date = run_date
        template.next_run_date = _step(template, anchor_day, run_date)
    if created:
        if template.end_date and template.next_run_date > template.end_date:
            template.is_active = False
        template.save(update_fields=["last_run_date", "next_run_date", "is_active", "updated_at"])
    return created


def generate_all_due(company, *, as_of=None, user=None):
    """[(template, [entries] | Exception)] for every active template that is due."""
    as_of = as_of or timezone.localdate()
    results = []
    for template in RecurringJournal.objects.filter(company=company, is_active=True, next_run_date__lte=as_of):
        try:
            results.append((template, generate_recurring(template, as_of=as_of, user=user)))
        except Exception as exc:                      # noqa: BLE001 - report it, keep going with the others
            results.append((template, exc))
    return results
