from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from audit.services import log_action
from .forms import PaymentMethodForm
from .models import PaymentMethod


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


@login_required
@permission_required("payments.view_paymentmethod", raise_exception=True)
def method_list(request):
    PaymentMethod.ensure_defaults(request.store)
    return render(request, "payments/method_list.html", {"methods": PaymentMethod.objects.filter(store=request.store)})


@login_required
def method_form(request, pk=None):
    if not request.user.has_perm("payments.change_paymentmethod" if pk else "payments.add_paymentmethod"):
        raise PermissionDenied
    instance = get_object_or_404(PaymentMethod, pk=pk, store=request.store) if pk else None
    if request.method == "GET" and not _is_ajax(request):
        return redirect("payments:method_list")
    if request.method == "POST":
        form = PaymentMethodForm(request.POST, instance=instance, store=request.store)
        if form.is_valid():
            method = form.save(commit=False)
            method.store = request.store
            method.save()
            log_action(AuditLog.UPDATED if instance else AuditLog.CREATED, method, user=request.user,
                       note=f"Payment method {'updated' if instance else 'created'}: {method.name}")
            return JsonResponse({"ok": True, "message": f"Payment method “{method.name}” saved."})
    else:
        form = PaymentMethodForm(instance=instance, store=request.store,
                                 initial={} if instance else {"is_active": True, "sort_order": 100})
    return render(request, "payments/_method_form.html", {"form": form, "method": instance})


@login_required
@permission_required("payments.change_paymentmethod", raise_exception=True)
@require_POST
def method_toggle(request, pk):
    method = get_object_or_404(PaymentMethod, pk=pk, store=request.store)
    if method.is_active and not PaymentMethod.objects.filter(store=request.store, is_active=True).exclude(pk=pk).exists():
        messages.error(request, "At least one payment method must stay enabled.")
    else:
        method.is_active = not method.is_active
        method.save(update_fields=["is_active", "updated_at"])
        log_action(AuditLog.UPDATED, method, user=request.user,
                   note=f"Payment method {'enabled' if method.is_active else 'disabled'}: {method.name}")
        messages.success(request, f"{method.name} is now {'enabled' if method.is_active else 'disabled'}.")
    return redirect("payments:method_list")
