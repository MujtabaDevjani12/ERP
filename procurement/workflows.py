"""Approval bindings and QC hook for procurement documents."""
from approvals.services import bind_status, final_approver, register_handler
from quality.services import register_qc_handler


def _set(value):
    def _inner(request):
        doc = request.content_object
        if doc is not None:
            doc.status = value
            doc.save(update_fields=["status", "updated_at"])
    return _inner


def _po_approved(request):
    ext = request.content_object
    ext.status = "approved"
    ext.save(update_fields=["status", "updated_at"])
    from suppliers.models import PurchaseOrder
    PurchaseOrder.objects.filter(pk=ext.purchase_order_id).update(status="ordered")


def _invoice_approved(request):
    from .services import post_purchase_invoice
    post_purchase_invoice(request.content_object, user=final_approver(request))


def _payment_approved(request):
    from .services import post_vendor_payment
    post_vendor_payment(request.content_object, user=final_approver(request))


def _grn_qc(check):
    from .services import _on_grn_line_qc
    _on_grn_line_qc(check)


bind_status("purchase_requisition", pending="pending_approval", approved="approved", rejected="rejected", returned="draft")
# bind_status() records which status means "waiting"; register_handler() then replaces the approve/reject actions.
for _doc in ("purchase_order", "purchase_invoice", "vendor_payment"):
    bind_status(_doc, pending="pending_approval", approved="approved", rejected="rejected", returned="draft")
register_handler("purchase_order", on_approved=_po_approved, on_rejected=_set("rejected"), on_returned=_set("draft"))
register_handler("purchase_invoice", on_approved=_invoice_approved, on_rejected=_set("rejected"), on_returned=_set("draft"))
register_handler("vendor_payment", on_approved=_payment_approved, on_rejected=_set("rejected"), on_returned=_set("draft"))
register_qc_handler("grn_line", _grn_qc)
