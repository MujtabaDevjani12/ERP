from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import ListView

from core.listing import PagedListMixin
from sales.models import Sale
from .models import DocumentLog
from .services import render_invoice_pdf, send_invoice_email, send_whatsapp_invoice


@login_required
@permission_required("sales.view_sale", raise_exception=True)
def invoice_pdf_view(request, pk):
    """Streams the invoice as a PDF, opened inline
    in a new tab from the sale detail page."""
    sale = get_object_or_404(Sale, pk=pk, store=request.store)
    try:
        pdf_bytes = render_invoice_pdf(sale)
    except Exception as exc:  # noqa: BLE001 — WeasyPrint / system deps missing on this machine
        messages.error(request, f"Could not generate PDF: {exc}")
        DocumentLog.objects.create(sale=sale, channel="pdf_download", status="failed", error_message=str(exc)[:255])
        return redirect("sales:sale_detail", pk=pk)

    DocumentLog.objects.create(sale=sale, channel="pdf_download", status="sent")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{sale.reference}.pdf"'
    return response


@login_required
@permission_required("sales.view_sale", raise_exception=True)
def send_email_view(request, pk):
    sale = get_object_or_404(Sale, pk=pk, store=request.store)
    default_email = sale.customer.email if sale.customer else ""

    if request.method == "POST":
        to_email = request.POST.get("email", "").strip()
        if not to_email:
            messages.error(request, "Email address is required.")
        else:
            log = send_invoice_email(sale, to_email)
            if log.status == "sent":
                messages.success(request, f"Invoice emailed to {to_email}.")
            else:
                messages.error(request, f"Email failed: {log.error_message}")
        return redirect("sales:sale_detail", pk=pk)

    return render(request, "documents/send_email.html", {"sale": sale, "default_email": default_email})


@login_required
@permission_required("sales.view_sale", raise_exception=True)
def send_whatsapp_view(request, pk):
    sale = get_object_or_404(Sale, pk=pk, store=request.store)
    default_phone = sale.customer.phone if sale.customer else ""

    if request.method == "POST":
        to_phone = request.POST.get("phone", "").strip()
        if not to_phone:
            messages.error(request, "Phone number is required.")
        else:
            log = send_whatsapp_invoice(sale, to_phone)
            if log.status == "sent":
                messages.success(request, f"Invoice sent on WhatsApp to {to_phone}.")
            else:
                messages.error(request, f"WhatsApp send failed: {log.error_message}")
        return redirect("sales:sale_detail", pk=pk)

    return render(request, "documents/send_whatsapp.html", {"sale": sale, "default_phone": default_phone})


class DocumentLogListView(PagedListMixin, ListView):
    """Server-side filters / pagination."""
    filter_params = ("q", "status", "channel")
    model = DocumentLog
    template_name = "documents/document_log_list.html"
    context_object_name = "logs"

    def get_queryset(self):
        qs = DocumentLog.objects.filter(sale__store=self.request.store).select_related("sale").order_by("-created_at")
        g = self.request.GET
        if g.get("q"):
            qs = qs.filter(Q(sale__reference__icontains=g["q"]) | Q(recipient__icontains=g["q"]))
        if g.get("status"):
            qs = qs.filter(status=g["status"])
        if g.get("channel"):
            qs = qs.filter(channel=g["channel"])
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({"status_choices": DocumentLog.STATUS_CHOICES, "channel_choices": DocumentLog.CHANNEL_CHOICES})
        return ctx


document_log_list = login_required(
    permission_required("sales.view_sale", raise_exception=True)(DocumentLogListView.as_view())
)
