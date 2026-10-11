"""
    check = create_check(company, "incoming", "grn_line", line.pk, variant, qty, source_reference="GRN-2026-00001")
    complete_check(check, user, accepted=90, rejected=10, defects=[(defect_type, 10, "stained")])

Other apps register what should happen on the outcome:
    register_qc_handler("grn_line", fn)      # fn(check) is called inside the same transaction
"""
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action
from core.errors import ERPError
from core.money import q3
from numbering.services import next_reference
from .models import QualityCheck, QualityDefect, QualityParameter

_handlers = {}


def register_qc_handler(source_type, fn):
    _handlers[source_type] = fn


@transaction.atomic
def create_check(company, check_type, source_type, source_id, variant, quantity, *, source_reference="", parameters=None, user=None):
    quantity = q3(quantity)
    if quantity <= 0:
        raise ERPError("Quantity to inspect must be greater than zero.")
    existing = QualityCheck.objects.filter(source_type=source_type, source_id=str(source_id), check_type=check_type,
                                           status__in=[QualityCheck.PENDING, QualityCheck.HOLD]).first()
    if existing:
        return existing
    check = QualityCheck.objects.create(
        company=company, reference=next_reference(company, "quality_check"), check_type=check_type,
        source_type=source_type, source_id=str(source_id), source_reference=source_reference, variant=variant, quantity=quantity)
    for p in parameters or []:
        QualityParameter.objects.create(quality_check=check, name=p["name"], specification=p.get("specification", ""))
    log_action(AuditLog.CREATED, check, user=user, document_number=check.reference, company=company)
    return check


@transaction.atomic
def put_on_hold(check, user=None, remarks=""):
    check = QualityCheck.objects.select_for_update().get(pk=check.pk)
    if check.status != QualityCheck.PENDING:
        raise ERPError("Only a pending check can be put on hold.")
    check.status, check.remarks = QualityCheck.HOLD, remarks
    check.save(update_fields=["status", "remarks", "updated_at"])
    log_action(AuditLog.OTHER, check, user=user, note="put on hold", document_number=check.reference, company=check.company)
    return check


@transaction.atomic
def complete_check(check, user, *, accepted, rejected, defects=None, parameters=None, remarks=""):
    """accepted + rejected must equal the quantity presented. Held checks can be completed too."""
    check = QualityCheck.objects.select_for_update().get(pk=check.pk)
    if check.status not in (QualityCheck.PENDING, QualityCheck.HOLD):
        raise ERPError("This quality check is already completed.")
    accepted, rejected = q3(accepted), q3(rejected)
    if accepted < 0 or rejected < 0:
        raise ERPError("Quantities cannot be negative.")
    if accepted + rejected != check.quantity:
        raise ERPError(f"Accepted ({accepted}) + rejected ({rejected}) must equal the inspected quantity ({check.quantity}).")
    if rejected > 0 and not defects and not (remarks or "").strip():
        raise ERPError("Record at least one defect or a remark when rejecting quantity.")
    for defect_type, qty, note in defects or []:
        QualityDefect.objects.create(quality_check=check, defect_type=defect_type, quantity=q3(qty), note=note)
    for name, measured, ok in parameters or []:
        QualityParameter.objects.filter(quality_check=check, name=name).update(measured_value=str(measured), passed=ok)
    check.accepted_quantity, check.rejected_quantity = accepted, rejected
    check.status = QualityCheck.PASSED if rejected == 0 else (QualityCheck.FAILED if accepted == 0 else QualityCheck.PARTIAL)
    check.inspector = user if getattr(user, "is_authenticated", False) else None
    check.inspected_at, check.remarks = timezone.now(), remarks
    check.save()
    log_action(AuditLog.OTHER, check, user=user, note=f"result {check.status}", document_number=check.reference, company=check.company)
    handler = _handlers.get(check.source_type)
    if handler:
        handler(check)
    return check


def pending_checks(company, check_type=None):
    qs = QualityCheck.objects.filter(company=company, status__in=[QualityCheck.PENDING, QualityCheck.HOLD]).select_related("variant")
    return qs.filter(check_type=check_type) if check_type else qs


def defect_summary(company, date_from=None, date_to=None):
    from django.db.models import Sum
    qs = QualityDefect.objects.filter(quality_check__company=company)
    if date_from:
        qs = qs.filter(quality_check__inspected_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(quality_check__inspected_at__date__lte=date_to)
    return list(qs.values("defect_type__name", "defect_type__severity").annotate(total=Sum("quantity")).order_by("-total"))
