"""
Reusable approval engine.

    # in the module that owns the document (once, e.g. AppConfig.ready):
    bind_status("purchase_order", pending="pending_approval", approved="approved",
                rejected="rejected", returned="draft")

    # when the user clicks Submit:
    submit_for_approval(po, "purchase_order", company=company, user=request.user,
                        amount=po.total_amount, number=po.reference)

    # approvers:
    approve(request_obj, user, comment)   reject(...)   return_for_correction(...)
    delegate(request_obj, user, to_user, comment)

If no active workflow matches, the request is approved immediately (and the
document status is set to the `approved` value), so unconfigured document
types do not block the business.
"""
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action
from core.errors import ApprovalError
from .models import ApprovalAction, ApprovalAssignment, ApprovalRequest, ApprovalWorkflow

_handlers = {}
_status_map = {}


# ------------------------------------------------------------ registration
def register_handler(document_type, *, on_approved=None, on_rejected=None, on_returned=None):
    """Callbacks receive the ApprovalRequest. Registering again replaces."""
    _handlers[document_type] = {
        "approved": on_approved, "rejected": on_rejected, "returned": on_returned,
    }


def bind_status(document_type, *, pending="pending_approval", approved="approved",
                rejected="rejected", returned="draft", field="status"):
    """Convenience: keep the document's `status` field in sync with the
    approval outcome without writing handler functions."""
    _status_map[document_type] = {"pending": pending, "field": field}

    def _setter(value):
        def _inner(request):
            doc = request.content_object
            if doc is not None:
                setattr(doc, field, value)
                doc.save(update_fields=[field, "updated_at"] if hasattr(doc, "updated_at") else [field])
        return _inner

    register_handler(document_type, on_approved=_setter(approved),
                     on_rejected=_setter(rejected), on_returned=_setter(returned))


def _fire(request, event):
    callback = _handlers.get(request.document_type, {}).get(event)
    if callback:
        callback(request)


# ------------------------------------------------------------ lookups
def find_workflow(company, document_type, amount=0):
    """Active workflow with the highest min_amount that the amount reaches."""
    return (
        ApprovalWorkflow.objects.filter(
            company=company, document_type=document_type, is_active=True, min_amount__lte=amount,
        ).exclude(levels__isnull=True).order_by("-min_amount").first()
    )


def get_request(document, document_type=None):
    qs = ApprovalRequest.objects.filter(
        content_type=ContentType.objects.get_for_model(document, for_concrete_model=False),
        object_id=document.pk,
    )
    if document_type:
        qs = qs.filter(document_type=document_type)
    return qs.order_by("-id").first()


def _level_obj(request):
    if request.workflow_id is None:
        return None
    return request.workflow.levels.filter(level=request.current_level).first()


def can_act(user, request):
    if request.status != ApprovalRequest.PENDING:
        return False
    if user.is_superuser:
        return True
    level = _level_obj(request)
    if level is None:
        return False
    if level.approver_user_id == user.id:
        return True
    if level.approver_group_id and user.groups.filter(pk=level.approver_group_id).exists():
        return True
    return ApprovalAssignment.objects.filter(request=request, level=request.current_level, user=user).exists()


def pending_for(user):
    """Requests currently waiting on this user (for an 'approvals inbox')."""
    return [r for r in ApprovalRequest.objects.filter(status=ApprovalRequest.PENDING).select_related("workflow")
            if can_act(user, r)]


# ------------------------------------------------------------ actions
def _record(request, action, user, comment="", level=None, delegated_to=None):
    return ApprovalAction.objects.create(
        request=request, level=request.current_level if level is None else level,
        actor=user if (user is not None and getattr(user, "is_authenticated", False)) else None,
        action=action, comment=comment, delegated_to=delegated_to,
    )


@transaction.atomic
def submit_for_approval(document, document_type, *, company, user=None, amount=0, number=""):
    pending = _status_map.get(document_type)
    if pending and hasattr(document, pending["field"]):
        setattr(document, pending["field"], pending["pending"])
        document.save(update_fields=[pending["field"], "updated_at"] if hasattr(document, "updated_at") else [pending["field"]])
    return submit(document, document_type, company=company, user=user, amount=amount, number=number)


@transaction.atomic
def submit(document, document_type, *, company, user=None, amount=0, number=""):
    ctype = ContentType.objects.get_for_model(document, for_concrete_model=False)
    if ApprovalRequest.objects.filter(content_type=ctype, object_id=document.pk,
                                      document_type=document_type, status=ApprovalRequest.PENDING).exists():
        raise ApprovalError("This document is already waiting for approval.")

    workflow = find_workflow(company, document_type, amount)
    first_level = workflow.levels.order_by("level").first() if workflow else None
    request = ApprovalRequest.objects.create(
        company=company, workflow=workflow, content_type=ctype, object_id=document.pk,
        document_type=document_type, document_number=str(number)[:40], amount=amount,
        current_level=first_level.level if first_level else 0,
        submitted_by=user if (user is not None and getattr(user, "is_authenticated", False)) else None,
    )
    _record(request, ApprovalAction.SUBMITTED, user, level=0)
    log_action(AuditLog.OTHER, request, user=user, note=f"submitted {document_type} {number}",
               document_number=str(number), company=company)

    if workflow is None or first_level is None:
        _finish(request, ApprovalRequest.APPROVED, None, "No approval workflow configured: auto-approved.")
    return request


def _finish(request, status, user, comment):
    request.status = status
    request.completed_at = timezone.now()
    request.save(update_fields=["status", "completed_at", "updated_at"])
    action = {
        ApprovalRequest.APPROVED: ApprovalAction.APPROVED,
        ApprovalRequest.REJECTED: ApprovalAction.REJECTED,
        ApprovalRequest.RETURNED: ApprovalAction.RETURNED,
    }[status]
    _record(request, action, user, comment)
    audit_action = {
        ApprovalRequest.APPROVED: AuditLog.APPROVED,
        ApprovalRequest.REJECTED: AuditLog.REJECTED,
        ApprovalRequest.RETURNED: AuditLog.OTHER,
    }[status]
    log_action(audit_action, request, user=user, note=comment[:255], document_number=request.document_number,
               company=request.company)
    _fire(request, {"approved": "approved", "rejected": "rejected", "returned": "returned"}[status])


def _locked(request):
    return ApprovalRequest.objects.select_for_update().select_related("workflow").get(pk=request.pk)


@transaction.atomic
def approve(request, user, comment=""):
    request = _locked(request)
    if request.status != ApprovalRequest.PENDING:
        raise ApprovalError("This request is not pending.")
    if not can_act(user, request):
        raise ApprovalError("You are not an approver for the current level.")
    wf = request.workflow
    if (wf and not wf.allow_self_approval and request.submitted_by_id == user.id and not user.is_superuser):
        raise ApprovalError("You cannot approve a document you submitted yourself.")

    _record(request, ApprovalAction.APPROVED, user, comment)
    log_action(AuditLog.OTHER, request, user=user, note=f"level {request.current_level} approved",
               document_number=request.document_number, company=request.company)
    next_level = wf.levels.filter(level__gt=request.current_level).order_by("level").first() if wf else None
    if next_level:
        request.current_level = next_level.level
        request.save(update_fields=["current_level", "updated_at"])
        return request
    # final level: mark approved (do not double-record the action row)
    request.status = ApprovalRequest.APPROVED
    request.completed_at = timezone.now()
    request.save(update_fields=["status", "completed_at", "updated_at"])
    log_action(AuditLog.APPROVED, request, user=user, note=comment[:255],
               document_number=request.document_number, company=request.company)
    _fire(request, "approved")
    return request


@transaction.atomic
def reject(request, user, comment):
    if not (comment or "").strip():
        raise ApprovalError("A comment is required when rejecting.")
    request = _locked(request)
    if request.status != ApprovalRequest.PENDING:
        raise ApprovalError("This request is not pending.")
    if not can_act(user, request):
        raise ApprovalError("You are not an approver for the current level.")
    _finish(request, ApprovalRequest.REJECTED, user, comment)
    return request


@transaction.atomic
def return_for_correction(request, user, comment):
    if not (comment or "").strip():
        raise ApprovalError("A comment is required when returning a document.")
    request = _locked(request)
    if request.status != ApprovalRequest.PENDING:
        raise ApprovalError("This request is not pending.")
    if not can_act(user, request):
        raise ApprovalError("You are not an approver for the current level.")
    _finish(request, ApprovalRequest.RETURNED, user, comment)
    return request


@transaction.atomic
def resubmit(request, user=None):
    """After a 'returned' request was corrected: send it back into the flow."""
    request = _locked(request)
    if request.status != ApprovalRequest.RETURNED:
        raise ApprovalError("Only a returned request can be resubmitted.")
    first = request.workflow.levels.order_by("level").first() if request.workflow else None
    request.status = ApprovalRequest.PENDING
    request.completed_at = None
    request.current_level = first.level if first else 0
    request.save(update_fields=["status", "completed_at", "current_level", "updated_at"])
    _record(request, ApprovalAction.SUBMITTED, user, "Resubmitted after correction", level=0)
    pending = _status_map.get(request.document_type)
    doc = request.content_object
    if pending and doc is not None and hasattr(doc, pending["field"]):               # same guard as submit_for_approval
        setattr(doc, pending["field"], pending["pending"])
        doc.save(update_fields=[pending["field"], "updated_at"] if hasattr(doc, "updated_at") else [pending["field"]])
    return request


@transaction.atomic
def delegate(request, user, to_user, comment=""):
    request = _locked(request)
    if request.status != ApprovalRequest.PENDING:
        raise ApprovalError("This request is not pending.")
    if not can_act(user, request):
        raise ApprovalError("You are not an approver for the current level.")
    ApprovalAssignment.objects.get_or_create(request=request, level=request.current_level, user=to_user)
    _record(request, ApprovalAction.DELEGATED, user, comment, delegated_to=to_user)
    return request


@transaction.atomic
def cancel_request(request, user, comment=""):
    request = _locked(request)
    if request.status != ApprovalRequest.PENDING:
        raise ApprovalError("Only a pending request can be cancelled.")
    if not (user.is_superuser or request.submitted_by_id == user.id):
        raise ApprovalError("Only the submitter can cancel the request.")
    request.status = ApprovalRequest.CANCELLED
    request.completed_at = timezone.now()
    request.save(update_fields=["status", "completed_at", "updated_at"])
    _record(request, ApprovalAction.CANCELLED, user, comment)
    return request


def final_approver(request):
    """The user who gave the last approval (for documents that post on approval)."""
    action = request.actions.filter(action=ApprovalAction.APPROVED, actor__isnull=False).order_by("-id").first()
    return action.actor if action else None
