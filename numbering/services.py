"""
Configurable document numbering: PO-2026-00001, EMP-00001, ...

    from numbering.services import next_reference
    ref = next_reference(company, "purchase_order", store=branch)

Resolution order (first match wins):
  1. branch-specific sequence for the fiscal year
  2. company-wide sequence for the fiscal year
  3. branch-specific non-resetting sequence (fiscal_year = 0)
  4. company-wide non-resetting sequence (fiscal_year = 0)
  5. otherwise a company-wide sequence is created from DEFAULTS.

Counter bump is a compare-and-swap UPDATE, so it is safe under concurrent
requests on SQLite and PostgreSQL alike. Call it inside the same
transaction.atomic() as the document insert: if the document fails, the
number is rolled back with it (no gaps).
"""
from datetime import date
from typing import Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from .constants import DEFAULTS
from .models import DocumentSequence

_MAX_ATTEMPTS = 25


def _find_sequence(company, store, document_type, fiscal_year):
    base = DocumentSequence.objects.filter(company=company, document_type=document_type)
    for year in (fiscal_year, 0):
        rows = base.filter(fiscal_year=year)
        if store is not None:
            hit = rows.filter(store=store).first()
            if hit:
                return hit
        hit = rows.filter(store__isnull=True).first()
        if hit:
            return hit
    return None


def _create_default(company, document_type, fiscal_year):
    prefix, include_year, padding = DEFAULTS[document_type]
    # Most documents keep one GLOBALLY unique reference column, so two companies must never hand out the same text: the first company keeps the
    # plain prefix (PO-2026-00001); every later company gets its own tag (POC7-2026-00001).
    mine = DocumentSequence.objects.filter(company=company, document_type=document_type, store__isnull=True).order_by("-fiscal_year").first()
    if mine is not None:
        prefix = mine.prefix                  # a new fiscal year keeps the company's own prefix
    elif DocumentSequence.objects.filter(document_type=document_type).exclude(company=company).exists():
        prefix = f"{prefix}C{company.pk}"[:15]
    year_key = fiscal_year if include_year else 0
    try:
        with transaction.atomic():
            return DocumentSequence.objects.create(
                company=company, store=None, document_type=document_type, fiscal_year=year_key,
                prefix=prefix, include_year=include_year, padding=padding,
            )
    except IntegrityError:
        # another request created it first; fetch theirs
        return DocumentSequence.objects.get(
            company=company, store__isnull=True, document_type=document_type, fiscal_year=year_key,
        )


def next_reference(company, document_type: str, *, store=None, on_date: Optional[date] = None) -> str:
    if document_type not in DEFAULTS:
        raise ValueError(f"Unknown document type '{document_type}'.")
    if company is None:
        raise ValueError("A company is required to generate a document number.")

    on_date = on_date or timezone.localdate()
    fiscal_year = company.fiscal_year_for(on_date)

    for _ in range(_MAX_ATTEMPTS):
        seq = _find_sequence(company, store, document_type, fiscal_year) or _create_default(company, document_type, fiscal_year)
        number = seq.next_number
        bumped = DocumentSequence.objects.filter(pk=seq.pk, next_number=number).update(
            next_number=number + 1, updated_at=timezone.now(),
        )
        if bumped:
            return seq.format(number, fiscal_year)
        # someone else took this number between our read and update: retry
    raise RuntimeError(f"Could not allocate a '{document_type}' number after {_MAX_ATTEMPTS} attempts.")
