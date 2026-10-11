"""Paying a small expense out of the till from the POS screen (milk for the staff tea, a courier fee ...). It is a normal expense,
tied to the open register session so the shift's expected cash goes down by the same amount."""
import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from audit.services import log_action
from configuration.models import NumberingPrefix
from register.services import session_summary
from register.views import get_active_session
from .models import Expense, ExpenseCategory


def _fail(message, status=400, **extra):
    return JsonResponse({"ok": False, "error": message, **extra}, status=status)


@login_required
@permission_required("expenses.add_expense", raise_exception=True)
@require_POST
def pos_expense(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return _fail("Bad request body.")
    session = get_active_session(request)
    if session is None:
        return _fail("No register is open. Open a register to record an expense.", 409, code="no_register")
    if not session.register.can_be_used_by(request.user):
        return _fail("You are not assigned to this counter.", 403)
    raw_category = str(data.get("category_id") or "")
    category = ExpenseCategory.objects.filter(pk=int(raw_category), store=request.store).first() if raw_category.isdigit() and len(raw_category) < 10 else None
    if category is None:
        return _fail("Choose an expense category.", field="category")
    try:
        amount = Decimal(str(data.get("amount", "")).strip()).quantize(Decimal("0.01"))
        if not amount.is_finite() or amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        return _fail("Enter the amount as a number above zero.", field="amount")
    with transaction.atomic():
        held = Decimal(str(session_summary(session)["expected_cash"]))
        if amount > held:
            return _fail(f"The till only holds {held:.2f}, so it cannot pay out {amount:.2f}.", field="amount", till=str(held))
        expense = Expense.objects.create(
            reference=NumberingPrefix.next_reference(NumberingPrefix.EXPENSE), store=request.store, category=category, register_session=session,
            amount=amount, method="cash", paid_to=str(data.get("paid_to") or "").strip()[:150], expense_date=timezone.localdate(),
            note=str(data.get("note") or "").strip()[:1000], created_by=request.user)
        log_action(AuditLog.CREATED, expense, user=request.user, document_number=expense.reference,
                   note=f"Till expense on {session.register.name}: {category.name} {amount}")
    return JsonResponse({"ok": True, "reference": expense.reference, "amount": str(amount), "till": str(held - amount)})
