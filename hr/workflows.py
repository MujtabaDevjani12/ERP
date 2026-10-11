from approvals.services import bind_status, register_handler


def _leave_approved(request):
    from .services import apply_approved_leave
    req = request.content_object
    req.status = "approved"
    req.save(update_fields=["status", "updated_at"])
    apply_approved_leave(req)


def _loan_approved(request):
    from approvals.services import final_approver
    from .services import disburse_loan
    # status stays "pending_approval" until disburse_loan() flips it to active
    disburse_loan(request.content_object, user=final_approver(request))


def _set(value):
    def _inner(request):
        doc = request.content_object
        if doc is not None:
            doc.status = value
            doc.save(update_fields=["status", "updated_at"])
    return _inner


bind_status("leave_request", pending="pending_approval", approved="approved", rejected="rejected", returned="draft")
register_handler("leave_request", on_approved=_leave_approved, on_rejected=_set("rejected"), on_returned=_set("draft"))
bind_status("employee_loan", pending="pending_approval", approved="active", rejected="rejected", returned="draft")
register_handler("employee_loan", on_approved=_loan_approved, on_rejected=_set("rejected"), on_returned=_set("draft"))
