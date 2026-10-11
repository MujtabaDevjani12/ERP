"""Deliveries: getting a completed sale to the customer.

Packing -> On the way -> Delivered (or Cancelled). One delivery per sale. Everything is scoped to the signed-in branch
(request.store) and protected by the normal Django model permissions (sales.view_delivery / add_ / change_ / delete_)."""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import ListView
from urllib.parse import quote

from configuration.models import NumberingPrefix, SystemConfiguration
from core.listing import PagedListMixin
from .models import Delivery, Sale

# status -> statuses an ordinary staff member may move it to. Closed deliveries can only be reopened by a superuser.
FLOW = {
    "pending": {"out_for_delivery", "delivered", "cancelled"},
    "out_for_delivery": {"pending", "delivered", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
}


def _deliveries(request):
    return Delivery.objects.filter(sale__store=request.store).select_related("sale", "sale__customer", "delivery_person")


def _deliverable_sales(request):
    """Completed sales of this branch that do not have a delivery yet."""
    return Sale.objects.filter(store=request.store, status__in=["completed", "partially_returned"], delivery__isnull=True)


# ------------------------------------------------------------------------------------------------ list
class DeliveryListView(PagedListMixin, LoginRequiredMixin, PermissionRequiredMixin, ListView):
    permission_required = "sales.view_delivery"
    template_name = "sales/delivery_list.html"
    context_object_name = "deliveries"
    filter_params = ("q", "status", "df", "dt", "rider")

    def get_queryset(self):
        qs = _deliveries(self.request)
        g = self.request.GET
        if g.get("status"):
            qs = qs.filter(status=g["status"])
        if g.get("df"):
            qs = qs.filter(scheduled_date__gte=g["df"])
        if g.get("dt"):
            qs = qs.filter(scheduled_date__lte=g["dt"])
        if g.get("rider"):
            qs = qs.filter(Q(rider__icontains=g["rider"]) | Q(delivery_person__username__icontains=g["rider"]))
        q = (g.get("q") or "").strip()
        if q:
            qs = qs.filter(Q(reference__icontains=q) | Q(sale__reference__icontains=q) | Q(recipient_name__icontains=q)
                           | Q(phone__icontains=q) | Q(address__icontains=q) | Q(tracking_no__icontains=q))
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = _deliveries(self.request)
        today = timezone.localdate()
        counts = {r["status"]: r["n"] for r in base.values("status").annotate(n=Count("id"))}
        ctx.update({
            "status_choices": Delivery.STATUS_CHOICES,
            "n_packing": counts.get("pending", 0),
            "n_way": counts.get("out_for_delivery", 0),
            "n_done": counts.get("delivered", 0),
            "n_late": base.filter(status__in=["pending", "out_for_delivery"], scheduled_date__lt=today).count(),
            "today": today,
        })
        return ctx


# ------------------------------------------------------------------------------------------------ create / edit
class DeliveryForm(forms.ModelForm):
    sale_ref = forms.CharField(label="Sale / invoice number", required=False, help_text="Type the invoice number, e.g. SALE-000012.")
    delivery_person = forms.ModelChoiceField(label="Delivery person (staff)", queryset=get_user_model().objects.none(), required=False,
                                             empty_label="— not a staff member —")

    class Meta:
        model = Delivery
        fields = ["recipient_name", "phone", "address", "city", "scheduled_date", "delivery_person", "rider", "tracking_no", "note"]
        widgets = {
            "scheduled_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "note": forms.Textarea(attrs={"rows": 3}),
            "address": forms.TextInput(attrs={"placeholder": "House / street / area"}),
            "recipient_name": forms.TextInput(attrs={"placeholder": "Who is the parcel for?"}),
            "rider": forms.TextInput(attrs={"placeholder": "Rider, driver or courier company"}),
        }
        labels = {"scheduled_date": "Promised on", "tracking_no": "Tracking number", "rider": "Rider / courier"}

    def __init__(self, *args, request=None, sale=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.sale = sale
        User = get_user_model()
        qs = User.objects.filter(is_active=True)
        if request is not None and request.store is not None and hasattr(User, "store"):
            qs = qs.filter(Q(store=request.store) | Q(store__isnull=True))
        self.fields["delivery_person"].queryset = qs.order_by("username")
        self.fields["delivery_person"].label_from_instance = lambda u: u.get_full_name() or u.username
        if sale is not None or self.instance.pk:
            self.fields.pop("sale_ref")
        else:
            self.order_fields(["sale_ref"] + list(self.Meta.fields))
        self.fields["address"].required = True
        self.fields["recipient_name"].required = True

    def clean_sale_ref(self):
        ref = (self.cleaned_data.get("sale_ref") or "").strip()
        if not ref:
            raise forms.ValidationError("Enter the invoice number of the sale to deliver.")
        sale = _deliverable_sales(self.request).filter(reference__iexact=ref).first()
        if sale is None:
            if Sale.objects.filter(store=self.request.store, reference__iexact=ref).exists():
                raise forms.ValidationError("That sale cannot be delivered (it is not completed, or it already has a delivery).")
            raise forms.ValidationError("No sale with this invoice number in this branch.")
        self.sale = sale
        return ref


def _prefill(sale):
    c = sale.customer
    if c is None:
        return {}
    return {"recipient_name": c.name, "phone": c.phone or "", "address": c.address or ""}


@login_required
def delivery_form(request, pk=None):
    instance = None
    sale = None
    if pk is not None:
        instance = get_object_or_404(_deliveries(request), pk=pk)
        perm = "sales.change_delivery"
        if instance.is_closed and not request.user.is_superuser:
            messages.error(request, f"This delivery is already {instance.get_status_display().lower()} and can no longer be edited.")
            return redirect("sales:delivery_detail", pk=pk)
    else:
        perm = "sales.add_delivery"
        sid = request.GET.get("sale")
        if sid:
            sale = get_object_or_404(_deliverable_sales(request), pk=sid)
    if not request.user.has_perm(perm):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    initial = _prefill(sale) if sale is not None else {}
    form = DeliveryForm(request.POST or None, instance=instance, request=request, sale=sale, initial=initial)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            if instance is None:
                target = form.sale
                if Delivery.objects.filter(sale=target).exists():                 # lost a race with another cashier
                    messages.error(request, "That sale already has a delivery.")
                    return redirect("sales:delivery_detail", pk=target.delivery.pk)
                obj.sale = target
                obj.reference = NumberingPrefix.next_reference(NumberingPrefix.DELIVERY)
                obj.created_by = request.user
                if not (obj.recipient_name or "").strip() and target.customer_id:
                    obj.recipient_name = target.customer.name
            obj.save()
        messages.success(request, f"Delivery {obj.reference} saved.")
        return redirect("sales:delivery_detail", pk=obj.pk)

    sales_hint = list(_deliverable_sales(request).order_by("-created_at").values_list("reference", flat=True)[:30]) if instance is None and sale is None else []
    return render(request, "sales/delivery_form.html", {
        "form": form, "delivery": instance, "sale": sale or (instance.sale if instance else None), "sales_hint": sales_hint,
        "title": f"Edit {instance.reference}" if instance else "New delivery",
    })


# ------------------------------------------------------------------------------------------------ detail + actions
def _steps(delivery):
    order = ["pending", "out_for_delivery", "delivered"]
    if delivery.status == "cancelled":
        return [{"label": "Packing", "state": "done"}, {"label": "Cancelled", "state": "current"}]
    idx = order.index(delivery.status)
    labels = ["Packing", "On the way", "Delivered"]
    return [{"label": labels[i], "state": "done" if i < idx or delivery.status == "delivered" else ("current" if i == idx else "todo")} for i in range(3)]


@login_required
@permission_required("sales.view_delivery", raise_exception=True)
def delivery_detail(request, pk):
    delivery = get_object_or_404(_deliveries(request), pk=pk)
    sale = delivery.sale
    events = [{"text": "Delivery created", "time": delivery.created_at.strftime("%d %b %Y, %H:%M"),
               "actor": (delivery.created_by.get_full_name() or delivery.created_by.username) if delivery.created_by_id else "", "state": "done"}]
    if delivery.status == "out_for_delivery":
        events.append({"text": "Out for delivery", "time": delivery.updated_at.strftime("%d %b %Y, %H:%M"), "state": "current"})
    if delivery.delivered_at:
        events.append({"text": f"Delivered{' - received by ' + delivery.received_by if delivery.received_by else ''}",
                       "time": delivery.delivered_at.strftime("%d %b %Y, %H:%M"), "state": "done"})
    if delivery.status == "cancelled":
        events.append({"text": "Cancelled", "time": delivery.updated_at.strftime("%d %b %Y, %H:%M"), "state": "current"})
    allowed = FLOW[delivery.status] if not request.user.is_superuser else {"pending", "out_for_delivery", "delivered", "cancelled"} - {delivery.status}
    return render(request, "sales/delivery_detail.html", {
        "delivery": delivery, "sale": sale, "items": sale.items.select_related("variant", "variant__product"),
        "steps": _steps(delivery), "events": events, "allowed": allowed,
        "can_change": request.user.has_perm("sales.change_delivery"),
        "can_delete": request.user.has_perm("sales.delete_delivery") and delivery.status in ("pending", "cancelled"),
        "wa_url": _whatsapp_url(delivery),
    })


@login_required
@permission_required("sales.change_delivery", raise_exception=True)
@require_POST
def delivery_status(request, pk):
    delivery = get_object_or_404(_deliveries(request), pk=pk)
    new = request.POST.get("status", "")
    if new not in dict(Delivery.STATUS_CHOICES):
        messages.error(request, "Choose a valid status.")
        return redirect("sales:delivery_detail", pk=pk)
    if new == delivery.status:
        return redirect("sales:delivery_detail", pk=pk)
    if new not in FLOW[delivery.status] and not request.user.is_superuser:
        messages.error(request, f"A {delivery.get_status_display().lower()} delivery cannot be changed to {dict(Delivery.STATUS_CHOICES)[new].lower()}. Ask an admin to correct it.")
        return redirect("sales:delivery_detail", pk=pk)
    old = delivery.status
    delivery.status = new
    if new == "delivered":
        delivery.delivered_at = timezone.now()
        delivery.received_by = (request.POST.get("received_by") or delivery.recipient_name or "").strip()[:100]
    elif old == "delivered":
        delivery.delivered_at, delivery.received_by = None, ""
    note = (request.POST.get("note") or "").strip()
    if note:
        delivery.note = note[:500]
    delivery.save()
    messages.success(request, f"{delivery.reference} is now {delivery.get_status_display().lower()}.")
    return redirect("sales:delivery_detail", pk=pk)


@login_required
@permission_required("sales.delete_delivery", raise_exception=True)
@require_POST
def delivery_delete(request, pk):
    delivery = get_object_or_404(_deliveries(request), pk=pk)
    if delivery.status not in ("pending", "cancelled"):
        messages.error(request, "Only deliveries that are still being packed, or cancelled, can be deleted. Cancel it instead.")
        return redirect("sales:delivery_detail", pk=pk)
    ref = delivery.reference
    delivery.delete()
    messages.success(request, f"Delivery {ref} deleted.")
    return redirect("sales:delivery_list")


# ------------------------------------------------------------------------------------------------ delivery note + messages
@login_required
@permission_required("sales.view_delivery", raise_exception=True)
def delivery_note(request, pk):
    """A plain, print-ready delivery note (opens the browser's print dialog)."""
    delivery = get_object_or_404(_deliveries(request), pk=pk)
    sale = delivery.sale
    return render(request, "sales/delivery_note.html", {"delivery": delivery, "sale": sale, "items": sale.items.select_related("variant", "variant__product"), "store": sale.store})


def _message_text(delivery):
    cfg = SystemConfiguration.get()
    shop = cfg.site_name or delivery.sale.store.name
    text = f"Hello {delivery.recipient_name or 'customer'}, your order {delivery.sale.reference} from {shop} is {delivery.get_status_display().lower()}."
    if delivery.scheduled_date and delivery.status not in Delivery.CLOSED:
        text += f" Expected on {delivery.scheduled_date.strftime('%d %b %Y')}."
    if delivery.tracking_no:
        text += f" Tracking number: {delivery.tracking_no}."
    return text


def _whatsapp_url(delivery):
    from configuration.mailing import whatsapp_number
    phone = delivery.phone or (delivery.sale.customer.phone if delivery.sale.customer_id else "")
    number = whatsapp_number(phone, SystemConfiguration.get().whatsapp_country_code)
    if not number:
        return ""
    return f"https://wa.me/{number}?text={quote(_message_text(delivery))}"


@login_required
@permission_required("sales.view_delivery", raise_exception=True)
@require_POST
def delivery_email(request, pk):
    from django.core.mail import EmailMessage
    from django.core.validators import validate_email
    from django.core.exceptions import ValidationError
    from configuration.mailing import sender_identity
    from documents.models import DocumentLog

    delivery = get_object_or_404(_deliveries(request), pk=pk)
    to = (request.POST.get("email") or (delivery.sale.customer.email if delivery.sale.customer_id else "") or "").strip()
    try:
        validate_email(to)
    except ValidationError:
        messages.error(request, "Enter a valid email address.")
        return redirect("sales:delivery_detail", pk=pk)
    log = DocumentLog.objects.create(sale=delivery.sale, channel="email", recipient=to, status="pending")
    try:
        from_email, reply_to = sender_identity()
        EmailMessage(subject=f"Your delivery {delivery.reference}", body=_message_text(delivery) + "\n\nThank you!",
                     from_email=from_email, to=[to], reply_to=reply_to).send(fail_silently=False)
        log.status = "sent"
        messages.success(request, f"Delivery update emailed to {to}.")
    except Exception as exc:  # noqa: BLE001 - shown in the Document log
        log.status, log.error_message = "failed", str(exc)[:255]
        messages.error(request, f"Email failed: {log.error_message}")
    log.save(update_fields=["status", "error_message", "updated_at"])
    return redirect("sales:delivery_detail", pk=pk)
