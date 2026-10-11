from approvals.services import bind_status, register_handler
from quality.services import register_qc_handler


def _wo_approved(request):
    from .services import create_job_cards
    wo = request.content_object
    wo.status = "released"
    wo.save(update_fields=["status", "updated_at"])
    create_job_cards(wo)


def _set(value):
    def _inner(request):
        doc = request.content_object
        if doc is not None:
            doc.status = value
            doc.save(update_fields=["status", "updated_at"])
    return _inner


def _job_qc(check):
    from .services import _on_job_card_qc
    _on_job_card_qc(check)


def _receipt_qc(check):
    from .services import _on_receipt_qc
    _on_receipt_qc(check)


bind_status("work_order", pending="pending_approval", approved="released", rejected="rejected", returned="draft")
register_handler("work_order", on_approved=_wo_approved, on_rejected=_set("rejected"), on_returned=_set("draft"))
register_qc_handler("job_card", _job_qc)
register_qc_handler("production_receipt", _receipt_qc)
