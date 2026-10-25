from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from core.decorators import needs_any_perm
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import ListView

from audit.models import AuditLog
from core.listing import PagedListMixin
from audit.services import log_action
from . import services
from .forms import CounterForm
from .models import Register, RegisterSession

SESSION_KEY = "active_register_session_id"


def get_active_session(request):
    """The register session this browser is currently working under, or None. Cheap: one query only when the session
    id is present. A session that was closed elsewhere (or whose counter was switched off) is forgotten."""
    session_id = request.session.get(SESSION_KEY)
    if not session_id:
        return None
    session = RegisterSession.objects.filter(pk=session_id, status="open").select_related("register", "opened_by").first()
    if session is None:
        request.session.pop(SESSION_KEY, None)
    return session


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest" or "application/json" in request.headers.get("accept", "")


# ---------------------------------------------------------------- counter selection + open / resume
@needs_any_perm('sales.add_sale', 'register.view_registersession', 'register.add_registersession')
def register_open_view(request):
    """Counter selection screen. Each counter card either opens a new shift, or - when it already has an open shift -
    lets the right person continue it (so a logout / closed tab never strands a shift)."""
    store = request.store
    current = get_active_session(request)
    if current and request.method == "GET" and request.GET.get("switch") != "1":
        return redirect("dashboard:pos")

    counters = list(services.allowed_registers(request.user, store))
    open_by_register = {s.register_id: s for s in RegisterSession.objects.filter(
        register__in=counters, status="open").select_related("opened_by")}
    for c in counters:
        c.open_session = open_by_register.get(c.pk)
        c.can_resume = bool(c.open_session and services.can_resume(c.open_session, request.user))

    form_values = {"register": request.POST.get("register", ""), "opening_cash": request.POST.get("opening_cash", "0"),
                   "note": request.POST.get("note", "")}

    if request.method == "POST":
        action = request.POST.get("action", "open")
        try:
            register = next((c for c in counters if str(c.pk) == request.POST.get("register")), None)
            if register is None:
                raise services.RegisterError("Please select a counter that is assigned to you.")
            if action == "resume":
                if not register.open_session or not services.can_resume(register.open_session, request.user):
                    raise services.RegisterError("You can't continue this counter's register session.")
                session = register.open_session
                messages.success(request, f"Continuing {register.name}.")
            else:
                amount = services.parse_amount(request.POST.get("opening_cash"), "Opening cash")
                session = services.open_session(register, request.user, amount, request.POST.get("note", ""))
                messages.success(request, f"{register.name} is now open.")
            request.session[SESSION_KEY] = session.pk
            return redirect("dashboard:pos")
        except services.RegisterError as exc:
            messages.error(request, str(exc))

    return render(request, "register/open.html", {
        "counters": counters, "form_values": form_values, "current_session": current,
        "can_manage_counters": request.user.has_perm("register.change_register"),
    })


# ---------------------------------------------------------------- close register
@needs_any_perm('sales.add_sale', 'register.view_registersession', json=True)
@require_GET
def register_summary_api(request):
    session = get_active_session(request)
    if not session:
        return JsonResponse({"ok": False, "error": "No register is open."}, status=409)
    return JsonResponse({"ok": True, "summary": services.jsonable(services.session_summary(session))})


@needs_any_perm('sales.add_sale', 'register.view_registersession', 'register.change_registersession')
def register_close_view(request):
    session = get_active_session(request)
    if not session:
        if _is_ajax(request):
            return JsonResponse({"ok": False, "error": "No register is open.", "redirect": "/register/open/"}, status=409)
        return redirect("register:open")

    if request.method == "POST":
        try:
            counted = services.parse_amount(request.POST.get("closing_cash_counted"), "Counted cash")
            closed = services.close_session(session.pk, request.user, counted, request.POST.get("note", ""))
        except services.RegisterError as exc:
            if str(exc).endswith("already closed."):
                request.session.pop(SESSION_KEY, None)
            if _is_ajax(request):
                return JsonResponse({"ok": False, "error": str(exc)}, status=400)
            messages.error(request, str(exc))
            return render(request, "register/close.html", {"session": session, "summary": services.session_summary(session)})
        request.session.pop(SESSION_KEY, None)
        diff = closed.variance
        text = f"Register closed. Cash difference: {diff:+.2f}" if diff else "Register closed. Cash matches exactly."
        if _is_ajax(request):
            return JsonResponse({"ok": True, "message": text, "redirect": reverse("register:session_detail", args=[closed.pk]), "session_id": closed.pk})
        messages.success(request, text)
        return redirect("register:session_detail", pk=closed.pk)

    return render(request, "register/close.html", {"session": session, "summary": services.session_summary(session)})


# ---------------------------------------------------------------- shift history
class RegisterSessionListView(PagedListMixin, LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """Server-side filters / pagination."""
    filter_params = ("status", "register")
    model = RegisterSession
    permission_required = "register.view_registersession"
    template_name = "dashboard/register_session_list.html"
    context_object_name = "sessions"

    def get_queryset(self):
        qs = RegisterSession.objects.filter(register__store=self.request.store).select_related("register", "opened_by", "closed_by")
        g = self.request.GET
        if g.get("status") in ("open", "closed"):
            qs = qs.filter(status=g["status"])
        if g.get("register"):
            qs = qs.filter(register_id=g["register"])
        return qs.order_by("-opened_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["counters"] = Register.objects.filter(store=self.request.store).order_by("name")
        return ctx


@needs_any_perm('register.view_registersession')
def register_session_detail(request, pk):
    session = get_object_or_404(RegisterSession.objects.select_related("register", "opened_by", "closed_by"),
                                pk=pk, register__store=request.store)
    if session.status == "closed" and session.closing_summary:
        summary = session.closing_summary
        frozen = True
    else:
        summary = services.jsonable(services.session_summary(session))
        frozen = False
    return render(request, "register/session_detail.html", {"session": session, "summary": summary, "frozen": frozen})


# ---------------------------------------------------------------- counters management
def _counter_store(request):
    """The branch counters are managed for. Owners/admins who are not tied to one branch (user.store is empty) used to
    get a form with no warehouses and a counter with no branch, so they fall back to the first active branch."""
    store = request.store
    if store is None and request.user.is_superuser:
        from tenants.models import Store
        store = Store.objects.filter(is_active=True).order_by("pk").first()
    return store


def _counter_qs(store):
    return Register.objects.filter(store=store).select_related("warehouse").prefetch_related("assigned_users").annotate(
        open_now=Count("sessions", filter=Q(sessions__status="open"), distinct=True),
        shifts=Count("sessions", distinct=True))


@login_required
@permission_required("register.view_register", raise_exception=True)
def counter_list(request):
    from payments.models import PaymentMethod
    store = _counter_store(request)
    labels = PaymentMethod.label_map(store) if store else {}
    counters = list(_counter_qs(store)) if store else []
    for c in counters:
        c.default_method_name = labels.get(c.default_payment_method, "") if c.default_payment_method else ""
    return render(request, "register/counter_list.html", {"counters": counters})


@login_required
def counter_form(request, pk=None):
    perm = "register.change_register" if pk else "register.add_register"
    if not request.user.has_perm(perm):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    store = _counter_store(request)
    if store is None:
        messages.error(request, "Your account is not linked to a branch yet. Ask an administrator to assign you to one.")
        return redirect("register:counter_list")
    instance = get_object_or_404(Register, pk=pk, store=store) if pk else None
    if not _is_ajax(request) and request.method == "GET":
        return redirect("register:counter_list")

    if request.method == "POST":
        form = CounterForm(request.POST, instance=instance, store=store)
        if form.is_valid():
            counter = form.save(commit=False)
            counter.store = store
            counter.save()
            form.save_m2m()
            log_action(AuditLog.UPDATED if instance else AuditLog.CREATED, counter, user=request.user,
                       note=f"Counter {'updated' if instance else 'created'}: {counter.name}")
            return JsonResponse({"ok": True, "message": f"Counter “{counter.name}” saved."})
    else:
        initial = {} if instance else {"is_active": True}
        form = CounterForm(instance=instance, store=store, initial=initial)
    return render(request, "register/_counter_form.html", {"form": form, "counter": instance})


@login_required
@permission_required("register.change_register", raise_exception=True)
@require_POST
def counter_toggle(request, pk):
    counter = get_object_or_404(Register, pk=pk, store=_counter_store(request))
    if counter.is_active and counter.sessions.filter(status="open").exists():
        messages.error(request, f"{counter.name} has an open register session. Close it before switching the counter off.")
    else:
        counter.is_active = not counter.is_active
        counter.save(update_fields=["is_active", "updated_at"])
        log_action(AuditLog.UPDATED, counter, user=request.user,
                   note=f"Counter {'activated' if counter.is_active else 'deactivated'}: {counter.name}")
        messages.success(request, f"{counter.name} is now {'active' if counter.is_active else 'inactive'}.")
    return redirect("register:counter_list")
