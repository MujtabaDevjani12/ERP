from approvals.services import bind_status

# approval of a sales order simply confirms it
bind_status("sales_order", pending="pending_approval", approved="confirmed", rejected="rejected", returned="draft")
