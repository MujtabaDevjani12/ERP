"""Approval bindings for stock documents."""
from approvals.services import bind_status, final_approver, register_handler


def _adjustment_approved(request):
    from .services import post_adjustment
    post_adjustment(request.content_object, user=final_approver(request))
    order = request.content_object
    if order is not None and order.source_count_id:
        order.source_count.status = "posted"
        order.source_count.save(update_fields=["status", "updated_at"])


def _adjustment_status(value):
    def _inner(request):
        order = request.content_object
        if order is not None:
            order.status = value
            order.save(update_fields=["status", "updated_at"])
    return _inner


bind_status("stock_transfer", pending="pending_approval", approved="approved", rejected="rejected", returned="draft")
bind_status("stock_adjustment", pending="pending_approval", approved="posted", rejected="rejected", returned="draft")
register_handler("stock_adjustment", on_approved=_adjustment_approved,
                 on_rejected=_adjustment_status("rejected"), on_returned=_adjustment_status("draft"))
