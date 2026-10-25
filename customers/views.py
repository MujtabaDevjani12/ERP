from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import CharField, Count, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Cast, Concat
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from core.decorators import needs_any_perm
from django.views.decorators.http import require_GET
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from core.listing import PagedListMixin
from core.mixins import DeleteContextMixin, SimpleListMixin, StoreCrudMixin
from core.utils import is_valid_cnic
from .forms import CustomerForm, GiftCardForm
from . import giftcards as gc
from .models import Customer, GiftCard, GiftCardTransaction


class CustomerListView(PagedListMixin, LoginRequiredMixin, PermissionRequiredMixin, ListView):
    """Server-side search / status filter / pagination."""
    filter_params = ("q", "status", "group")
    model = Customer
    permission_required = "customers.view_customer"
    template_name = "customers/customer_list.html"
    context_object_name = "customers"

    def get_queryset(self):
        deposit = GiftCard.objects.filter(customer=OuterRef("pk"), code=Concat(Value(gc.DEPOSIT_PREFIX), Cast(OuterRef("pk"), CharField())))
        qs = Customer.objects.filter(store=self.request.store).select_related("group").annotate(deposit_balance=Subquery(deposit.values("current_balance")[:1]))
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(cnic__icontains=q) | Q(email__icontains=q))
        if self.request.GET.get("status") in ("active", "inactive"):
            qs = qs.filter(is_active=self.request.GET["status"] == "active")
        if self.request.GET.get("group", "").isdigit():
            qs = qs.filter(group_id=int(self.request.GET["group"]))
        return qs.order_by("name")

    def get_context_data(self, **kwargs):
        from pricing.models import CustomerGroup
        ctx = super().get_context_data(**kwargs)
        ctx["groups"] = CustomerGroup.objects.filter(store=self.request.store).order_by("name")
        return ctx


class CustomerCreateView(StoreCrudMixin, CreateView):
    model = Customer
    form_class = CustomerForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("customers:customer_list")
    permission_required = "customers.add_customer"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["store"] = self.request.store
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add customer", back_url=self.success_url, **kwargs)


class CustomerUpdateView(StoreCrudMixin, UpdateView):
    model = Customer
    form_class = CustomerForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("customers:customer_list")
    permission_required = "customers.change_customer"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["store"] = self.request.store
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit customer", back_url=self.success_url, **kwargs)


class CustomerDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Customer
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("customers:customer_list")
    permission_required = "customers.delete_customer"


@needs_any_perm('customers.add_customer', 'sales.add_sale', json=True)
def customer_quick_add(request):
    """Used by the POS screen's 'Add Customer' shortcut (Ctrl+Shift+A) —
    creates a full customer record (name, phone, email, CNIC, address,
    price/customer group) without leaving the sale."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Name is required"}, status=400)

    cnic = request.POST.get("cnic", "").strip()
    if cnic and not is_valid_cnic(cnic):
        return JsonResponse({"error": "CNIC should look like 35201-1234567-1"}, status=400)

    group_id = request.POST.get("group") or None
    customer = Customer.objects.create(
        store=request.store, name=name,
        phone=request.POST.get("phone", "").strip(),
        email=request.POST.get("email", "").strip(),
        cnic=cnic,
        address=request.POST.get("address", "").strip(),
        group_id=group_id,
    )
    return JsonResponse({
        "id": customer.id, "name": customer.name, "phone": customer.phone,
        "email": customer.email, "cnic": customer.cnic, "address": customer.address,
    })


# ---------------------------------------------------------------- Gift cards
class GiftCardListView(PagedListMixin, LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "customers.view_giftcard"
    template_name = "customers/giftcard_list.html"
    context_object_name = "cards"
    filter_params = ("q", "status")

    def get_queryset(self):
        qs = GiftCard.objects.filter(store=self.request.store).select_related("customer")
        g = self.request.GET
        if (g.get("q") or "").strip():
            q = g["q"].strip()
            qs = qs.filter(Q(code__icontains=q) | Q(customer__name__icontains=q) | Q(customer__phone__icontains=q))
        today = timezone.localdate()
        status = g.get("status")
        if status == "active":
            qs = qs.filter(is_active=True).filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today)).filter(current_balance__gt=0)
        elif status == "empty":
            qs = qs.filter(current_balance__lte=0)
        elif status == "expired":
            qs = qs.filter(expires_on__lt=today)
        elif status == "off":
            qs = qs.filter(is_active=False)
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        live = GiftCard.objects.filter(store=self.request.store, is_active=True, current_balance__gt=0).filter(Q(expires_on__isnull=True) | Q(expires_on__gte=today))
        agg = live.aggregate(n=Count("id"), owed=Sum("current_balance"))
        ctx.update({"n_live": agg["n"] or 0, "owed": agg["owed"] or 0, "today": today,
                    "status_choices": [("active", "Usable"), ("empty", "Used up"), ("expired", "Expired"), ("off", "Switched off")]})
        return ctx


class GiftCardCreateView(StoreCrudMixin, CreateView):
    model = GiftCard
    form_class = GiftCardForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("customers:giftcard_list")
    permission_required = "customers.add_giftcard"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["store"] = self.request.store
        return kwargs

    def form_valid(self, form):
        form.instance.store = self.request.store
        form.instance.current_balance = form.cleaned_data["initial_balance"]
        with transaction.atomic():
            response = super().form_valid(form)
            card = self.object
            GiftCardTransaction.objects.create(card=card, kind=GiftCardTransaction.ISSUE, amount=card.initial_balance, balance_after=card.current_balance,
                                               user=self.request.user, note="Card created")
        return response

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add gift card", back_url=self.success_url, **kwargs)


class GiftCardUpdateView(StoreCrudMixin, UpdateView):
    model = GiftCard
    form_class = GiftCardForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("customers:giftcard_list")
    permission_required = "customers.change_giftcard"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["store"] = self.request.store
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit gift card", back_url=self.success_url, **kwargs)


@login_required
@permission_required("customers.view_giftcard", raise_exception=True)
def giftcard_history(request, pk):
    card = get_object_or_404(GiftCard.objects.select_related("customer"), pk=pk, store=request.store)
    return render(request, "customers/giftcard_history.html", {
        "card": card, "transactions": card.transactions.select_related("sale", "user")[:200],
        "can_topup": request.user.has_perm("customers.change_giftcard")})


@login_required
def giftcard_topup(request, pk):
    if not request.user.has_perm("customers.change_giftcard"):
        raise PermissionDenied
    card = get_object_or_404(GiftCard, pk=pk, store=request.store)
    if request.method == "GET" and request.headers.get("x-requested-with") != "XMLHttpRequest":
        return redirect("customers:giftcard_list")
    error = ""
    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", "").strip() or "0")
            if not amount.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            error = "Enter the amount as a number."
        else:
            if not card.is_active:
                error = "This card is switched off. Switch it on before adding money."
            else:
                try:
                    updated = gc.top_up(card.pk, amount, user=request.user, note=request.POST.get("note", ""))
                except gc.GiftCardError as exc:
                    error = str(exc)
                else:
                    return JsonResponse({"ok": True, "message": f"Added {gc.money(amount)} to {updated.code}. New balance {updated.current_balance}."})
    return render(request, "customers/_giftcard_topup.html", {"card": card, "error": error, "posted": request.POST})


@login_required
def customer_deposit(request, pk):
    """Side drawer: take an advance payment from a customer (kept as a prepaid card the POS can spend)."""
    if not request.user.has_perm("customers.add_giftcard"):
        raise PermissionDenied
    customer = get_object_or_404(Customer, pk=pk, store=request.store)
    if request.method == "GET" and request.headers.get("x-requested-with") != "XMLHttpRequest":
        return redirect("customers:customer_list")
    card = GiftCard.objects.filter(code=gc.deposit_code(customer), store=request.store).first()
    error = ""
    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", "").strip() or "0")
            if not amount.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            error = "Enter the amount as a number."
        else:
            try:
                updated = gc.add_deposit(customer, amount, user=request.user, note=request.POST.get("note", ""))
            except gc.GiftCardError as exc:
                error = str(exc)
            else:
                return JsonResponse({"ok": True, "message": f"Deposit of {gc.money(amount)} saved for {customer.name}. Balance {updated.current_balance}."})
    return render(request, "customers/_customer_deposit.html", {"customer": customer, "card": card, "error": error, "posted": request.POST})


@login_required
@require_GET
def giftcard_lookup(request):
    """POS: is this card usable and what is left on it? Never returns another branch's card.
    `?customer=<id>` looks up that customer's deposit card instead of a typed number."""
    try:
        code = request.GET.get("code", "")
        if request.GET.get("customer"):
            cust = Customer.objects.filter(pk=request.GET["customer"] if request.GET["customer"].isdigit() else 0, store=request.store).first()
            if cust is None or not GiftCard.objects.filter(store=request.store, code=gc.deposit_code(cust)).exists():
                raise gc.GiftCardError("This customer has no deposit.")
            code = gc.deposit_code(cust)
        card = gc.find(request.store, code)
    except gc.GiftCardError as exc:
        return JsonResponse({"ok": False, "error": str(exc)})
    return JsonResponse({"ok": True, "code": card.code, "balance": str(card.current_balance),
                         "expires": card.expires_on.isoformat() if card.expires_on else "", "customer": card.customer.name if card.customer else ""})


class GiftCardDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = GiftCard
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("customers:giftcard_list")
    permission_required = "customers.delete_giftcard"


@needs_any_perm('customers.view_customer', 'sales.add_sale', json=True)
def customer_search(request):
    """POS customer picker: search this branch's customers by name / phone / email (15 best matches)."""
    q = request.GET.get("q", "").strip()
    customers = Customer.objects.filter(store=request.store, is_active=True)
    if q:
        customers = customers.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))
    customers = customers.order_by("name")[:15]
    return JsonResponse({"results": [{"id": c.id, "name": c.name, "phone": c.phone, "email": c.email} for c in customers]})
