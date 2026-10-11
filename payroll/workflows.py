from approvals.services import bind_status, final_approver, register_handler


def _approved(request):
    from .services import post_payroll
    post_payroll(request.content_object, user=final_approver(request))


def _set(value):
    def _inner(request):
        doc = request.content_object
        if doc is not None:
            doc.status = value
            doc.save(update_fields=["status", "updated_at"])
    return _inner


bind_status("payroll", pending="pending_approval", approved="approved", rejected="rejected", returned="draft")
register_handler("payroll", on_approved=_approved, on_rejected=_set("rejected"), on_returned=_set("draft"))
