import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView

from accounts.models import User
from core.listing import PagedListMixin
from audit.models import AuditLog
from audit.services import log_action
from catalog.models import ProductVariant
from configuration.models import NumberingPrefix, SystemConfiguration
from customers import giftcards as gift_cards
from customers.models import Customer, LoyaltyTransaction
from inventory.models import StockItem
from payments.models import PaymentMethod, Payment
from register.views import get_active_session
from .models import PosCart, Sale, SaleFeedback, SaleItem
from .pos_cart import clean_cart, revalidate
from .pricing import CartError, money, price_cart, to_decimal
from .pos_stock import OUT_OF_STOCK, STOCK_CHANGED, stock_effects


class SaleListView(PagedListMixin, LoginRequiredMixin, ListView):
    """Server-side search / status filter / pagination (25-100 rows per page) with summary tiles for the filtered set."""
    model = Sale
    template_name = "sales/sale_list.html"
    context_object_name = "sales"
    filter_params = ("q", "status", "biller", "df", "dt", "pay", "warehouse")

    def get_queryset(self):
        qs = Sale.objects.filter(store=self.request.store).select_related("biller", "customer", "feedback").prefetch_related("payments")
        df, dt = self.request.GET.get("df"), self.request.GET.get("dt")
        if df:
            qs = qs.filter(created_at__date__gte=df)
        if dt:
            qs = qs.filter(created_at__date__lte=dt)
        status = self.request.GET.get("status")
        biller = self.request.GET.get("biller")
        q = self.request.GET.get("q")
        if status:
            qs = qs.filter(status=status)
        if biller:
            qs = qs.filter(biller_id=biller)
        if q:
            qs = qs.filter(Q(reference__icontains=q) | Q(customer__name__icontains=q) | Q(customer__phone__icontains=q))
        warehouse = self.request.GET.get("warehouse")
        if warehouse and warehouse.isdigit():
            qs = qs.filter(warehouse_id=int(warehouse))
        pay = self.request.GET.get("pay")
        if pay in ("paid", "partial", "due"):
            from django.db.models import Sum
            from django.db.models.functions import Coalesce
            qs = qs.exclude(status__in=["held", "cancelled"]).annotate(
                _paid=Coalesce(Sum("payments__amount", filter=~Q(payments__status="voided")), Decimal("0")))
            qs = {"paid": qs.filter(_paid__gte=F("total_amount")), "due": qs.filter(_paid__lte=0),
                  "partial": qs.filter(_paid__gt=0, _paid__lt=F("total_amount"))}[pay]
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        from django.db.models import Sum
        ctx = super().get_context_data(**kwargs)
        sold = self.get_queryset().exclude(status__in=["held", "cancelled"])
        total = sold.aggregate(t=Sum("total_amount"))["t"] or 0
        collected = Payment.objects.filter(sale__in=sold).exclude(status="voided").aggregate(t=Sum("amount"))["t"] or 0
        from tenants.models import Warehouse
        ctx.update({"warehouses": Warehouse.objects.filter(store=self.request.store).order_by("name"),
                    "pay": self.request.GET.get("pay", ""), "warehouse_id": self.request.GET.get("warehouse", ""),
                    "status_choices": Sale.STATUS_CHOICES, "sum_total": total, "sum_collected": collected,
                    "sum_due": total - collected, "sum_orders": sold.count(),
                    "df": self.request.GET.get("df", ""), "dt": self.request.GET.get("dt", "")})
        return ctx


class SaleDetailView(LoginRequiredMixin, DetailView):
    model = Sale
    template_name = "sales/sale_detail.html"
    context_object_name = "sale"

    def get_queryset(self):
        return Sale.objects.filter(store=self.request.store)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["payment_methods"] = PaymentMethod.active_for(self.request.store)
        sale = ctx["sale"]
        ctx["returns"] = list(sale.returns.select_related("processed_by").prefetch_related("items"))
        ctx["can_deliver"] = sale.status in ("completed", "partially_returned") and not hasattr(sale, "delivery")
        from .returns import refund_value
        items = list(sale.items.select_related("variant__product").prefetch_related("return_items"))
        returned_total = Decimal("0")
        for it in items:                                           # how much of each line has come back, and what was given back for it
            it.returned_qty = sum((r.quantity for r in it.return_items.all()), Decimal("0"))
            if it.returned_qty > 0:
                returned_total += refund_value(it, it.returned_qty)
        ctx["items"], ctx["returned_total"] = items, returned_total
        first_payment = sale.payments.select_related("received_by").order_by("pk").first()
        cashier = (first_payment.received_by if first_payment and first_payment.received_by_id else None) or (sale.register_session.opened_by if sale.register_session_id else None)
        ctx["cashier"] = cashier
        ctx["feedback"] = SaleFeedback.objects.filter(sale=sale).select_related("created_by").first()
        ctx["can_feedback"] = sale.status not in ("held", "cancelled") and self.request.user.has_perm("sales.add_sale")
        return ctx


@login_required
@permission_required("sales.view_sale", raise_exception=True)
def invoice_print_view(request, pk):
    """A plain HTML, print-ready receipt (no WeasyPrint/PDF dependency).
    Opened in a new tab straight from the POS payment modal after a sale
    is finalized, and auto-triggers the browser's print dialog on load —
    this is what makes 'complete payment' immediately hand the cashier a
    printable bill, the way a real POS till does."""
    sale = get_object_or_404(Sale.objects.select_related("store", "customer", "biller", "warehouse"), pk=pk, store=request.store)
    # ?embed=1: the POS prints it from a hidden frame (no Print/Close buttons, the dialog still opens by itself)
    return render(request, "sales/invoice_print.html", {"sale": sale, "embed": request.GET.get("embed") == "1"})


_CartError = CartError
_money = money
_decimal = to_decimal


def _parse_payments(raw_payments, total, store, has_customer):
    """Validates the payment lines the cashier entered. Returns (lines, tendered, change) where each line is
    {method: PaymentMethod, amount (net of change), reference_no, note}. Only cash can be over-tendered (the excess is change)."""
    methods = {m.code: m for m in PaymentMethod.active_for(store)}
    lines = []
    for p in raw_payments or []:
        if not isinstance(p, dict):
            raise _CartError("A payment line is invalid.", "bad_payment")
        method = methods.get(str(p.get("method") or "cash"))
        if method is None:
            raise _CartError("That payment method is not available.", "bad_payment")
        amount = _money(_decimal(p.get("amount", 0), f"{method.name} amount", minimum=Decimal("0")))
        if amount <= 0:
            continue                                          # empty split rows are simply ignored
        reference_no = str(p.get("reference_no") or p.get("reference") or "").strip()[:100]
        if method.code == "gift_card":                         # the "reference" of a gift card payment is the card number itself
            try:
                card = gift_cards.find(store, reference_no)
            except gift_cards.GiftCardError as exc:
                raise _CartError(str(exc), "gift_card", method=method.code)
            if card.current_balance < amount:
                raise _CartError(f"Gift card {card.code} only has {card.current_balance} left. Lower the amount or split the payment.", "gift_card_balance",
                                 method=method.code, balance=str(card.current_balance))
            reference_no = card.code
        if method.requires_reference and not reference_no:
            raise _CartError(f"Enter the reference / transaction ID for {method.name}.", "reference_required", method=method.code)
        lines.append({"method": method, "amount": amount, "reference_no": reference_no, "note": str(p.get("note", ""))[:150]})

    tendered = sum((ln["amount"] for ln in lines), Decimal("0"))
    non_cash = sum((ln["amount"] for ln in lines if not ln["method"].is_cash), Decimal("0"))
    if non_cash > total:
        raise _CartError("Card / bank / wallet payments cannot be more than the amount due.", "overpay")
    change = max(tendered - total, Decimal("0"))
    left = change                                             # change always comes out of the cash lines
    for ln in reversed(lines):
        if left <= 0:
            break
        if ln["method"].is_cash:
            cut = min(left, ln["amount"])
            ln["amount"] -= cut
            left -= cut
    lines = [ln for ln in lines if ln["amount"] > 0]
    if tendered - change < total and not has_customer:
        raise _CartError(f"Payment is less than the total due ({total}). Add the missing amount or select a customer to sell on credit.", "underpaid",
                         due=str(total - (tendered - change)))
    return lines, tendered, change


def _share_links(sale, user):
    """Links the POS success screen offers after a sale: email the invoice, WhatsApp it, arrange a delivery."""
    from urllib.parse import quote
    links = {"email_url": "", "whatsapp_url": "", "delivery_url": "", "delivery_api": ""}
    if user is None or sale.status != "completed":
        return links
    if user.has_perm("sales.view_sale"):
        links["email_url"] = reverse("documents:send_email", args=[sale.pk]) if apps.is_installed("documents") else ""
        try:
            from configuration.mailing import whatsapp_number
            phone = sale.customer.phone if sale.customer_id else ""
            number = whatsapp_number(phone, SystemConfiguration.get().whatsapp_country_code) if phone else ""
            text = f"{sale.store.name}: invoice {sale.reference}, total {sale.store.currency_symbol} {sale.total_amount}. Thank you for shopping with us!"
            links["whatsapp_url"] = f"https://wa.me/{number or ''}?text={quote(text)}"      # no number: WhatsApp lets the cashier pick the contact
        except Exception:  # noqa: BLE001 - sharing is a convenience, it must never break a finished sale
            pass
    if user.has_perm("sales.add_delivery") and not hasattr(sale, "delivery"):
        links["delivery_url"] = reverse("sales:delivery_add") + f"?sale={sale.pk}"
        links["delivery_api"] = reverse("sales:pos_delivery", args=[sale.pk])         # the POS opens the delivery form as a popup instead
    return links


def _auto_print(store):
    """Settings > Receipt > 'Print automatically' (on unless the branch switched it off)."""
    from configuration.models import POSConfiguration
    value = POSConfiguration.objects.filter(store=store).values_list("auto_print_receipt", flat=True).first()
    return True if value is None else bool(value)


def _sale_response(sale, *, change=Decimal("0"), tendered=None, duplicate=False, user=None):
    paid = sale.paid_amount
    return JsonResponse({
        "auto_print": _auto_print(sale.store),
        **_share_links(sale, user),
        "ok": True, "duplicate": duplicate, "reference": sale.reference, "sale_id": sale.id, "status": sale.status,
        "total": str(sale.total_amount), "paid": str(paid), "tendered": str(tendered if tendered is not None else paid),
        "change": str(change), "customer": sale.customer.name if sale.customer_id else "",
        "receipt_url": f"/sales/{sale.id}/",
        "order_url": f"/sales/{sale.id}/",
        "print_url": f"/sales/{sale.id}/print/",
        "invoice_url": _invoice_url(sale),
        "payment_status": sale.payment_status, "due_amount": str(sale.due_amount),
    })


def _invoice_url(sale):
    try:
        return reverse("documents:invoice_pdf", args=[sale.id])
    except NoReverseMatch:
        return f"/sales/{sale.id}/print/"


def _error_response(exc):
    return JsonResponse({"ok": False, "error": str(exc), "code": exc.code, **exc.extra}, status=400)


@login_required
@permission_required("sales.add_sale", raise_exception=True)
@require_POST
def pos_finalize_sale(request):
    """The POS screen's checkout submit target. Body is JSON:
    {checkout_token, items: [{variant_id, quantity, unit_price, discount_amount}], order_discount: {type, value},
     customer_id, payments: [{method, amount, reference_no, note}], sale_note, staff_note, hold: bool}
    Returns {ok, reference, sale_id, total, paid, change, receipt/print/invoice/order urls} or {ok: False, error, code}.

    Safe to retry: the same `checkout_token` always returns the same sale and never creates a second one. Every number is
    validated before anything is saved, the whole sale (items, stock, payments, loyalty points, cart clean-up) is one
    transaction, and stock rows are locked and re-checked while they are decremented."""
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Bad request body."}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"ok": False, "error": "Bad request body."}, status=400)

    store = request.store
    token = str(payload.get("checkout_token") or "").strip()[:64] or None
    if token:                                                 # a double click / network retry of a sale that already went through
        earlier = Sale.objects.filter(checkout_token=token, store=store).select_related("customer").first()
        if earlier is not None:
            return _sale_response(earlier, duplicate=True, user=request.user)

    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        return JsonResponse({"ok": False, "error": "Cart is empty.", "code": "empty"}, status=400)

    session = get_active_session(request)
    if not session:
        return JsonResponse({"ok": False, "error": "No register is open. Open a register to continue.", "code": "no_register"}, status=409)
    if not session.register.can_be_used_by(request.user):
        return JsonResponse({"ok": False, "error": "You are not assigned to this counter.", "code": "forbidden"}, status=403)

    warehouse = session.register.warehouse
    system_config = SystemConfiguration.get()
    hold = bool(payload.get("hold"))

    try:
        with transaction.atomic():
            # who is billing / who is buying must belong to this branch
            biller_id = request.user.id
            if payload.get("biller_id") and str(payload["biller_id"]) != str(request.user.id):
                biller = User.objects.filter(pk=payload["biller_id"], is_biller=True, is_active=True).filter(
                    Q(store=store) | Q(store__isnull=True)).first()
                if biller is None:
                    raise _CartError("That biller is not available in this branch.")
                biller_id = biller.id
            customer_id = None
            if payload.get("customer_id"):
                if not Customer.objects.filter(pk=payload["customer_id"], store=store, is_active=True).exists():
                    raise _CartError("That customer does not belong to this branch.")
                customer_id = payload["customer_id"]

            # ---- pass 1: validate and price every line (nothing is written yet)
            priced = price_cart(store, items, payload.get("order_discount"), payload.get("shipping"))
            rows, overrides = priced["rows"], priced["overrides"]
            subtotal, discount_total, tax_total, total = priced["subtotal"], priced["discount"], priced["tax"], priced["total"]

            pay_lines, tendered, change = [], Decimal("0"), Decimal("0")
            if not hold:
                pay_lines, tendered, change = _parse_payments(payload.get("payments"), total, store, bool(customer_id))

            # ---- pass 2: write the sale
            sale = Sale.objects.create(
                store=store, warehouse=warehouse, register_session=session,
                reference=NumberingPrefix.next_reference(NumberingPrefix.SALE),
                biller_id=biller_id, customer_id=customer_id, status="held" if hold else "completed",
                sale_note=str(payload.get("sale_note", ""))[:2000], staff_note=str(payload.get("staff_note", ""))[:2000],
                checkout_token=token, subtotal=subtotal, discount_amount=discount_total, tax_amount=tax_total, total_amount=total,
                shipping_amount=priced["shipping"], rounding_amount=priced["rounding"],
            )
            for r in rows:
                variant = r["variant"]
                locked = []
                for stock_variant, needed in stock_effects(variant, r["quantity"]):         # a combo takes its parts off the shelf
                    stock_item = StockItem.objects.select_for_update().filter(variant=stock_variant, warehouse=warehouse).first()
                    available = stock_item.quantity if stock_item else Decimal("0")
                    if not hold:
                        if available <= 0:
                            raise _CartError(OUT_OF_STOCK, "out_of_stock", variant_id=variant.id, product=str(stock_variant))
                        if available < needed and not system_config.allow_over_selling:
                            raise _CartError(STOCK_CHANGED, "stock_changed", variant_id=variant.id, product=str(stock_variant), available=str(available))
                    locked.append((stock_item, needed))
                SaleItem.objects.create(sale=sale, variant=variant, quantity=r["quantity"], unit_price=r["unit_price"],
                                        discount_amount=r["discount"], tax_amount=r["tax"])
                if not hold:
                    for stock_item, needed in locked:
                        stock_item.adjust(-needed, reference_type="POS_SALE", reference_no=sale.reference, user=request.user,
                                          remarks="POS sale")

            payments = []
            for ln in pay_lines:
                payments.append(Payment.objects.create(
                    sale=sale, reference=NumberingPrefix.next_reference(NumberingPrefix.PAYMENT),
                    amount=ln["amount"], method=ln["method"].code, note=ln["note"], reference_no=ln["reference_no"],
                    register_session=session, status="completed", received_by=request.user,
                ))

            for pay in payments:                                  # spend the gift card for real (locked; fails the whole sale if the balance changed meanwhile)
                if pay.method == "gift_card":
                    try:
                        gift_cards.redeem(store, pay.reference_no, pay.amount, sale=sale, user=request.user, note=f"POS sale {sale.reference}")
                    except gift_cards.GiftCardError as exc:
                        raise _CartError(str(exc), "gift_card", method="gift_card")

            if overrides:
                log_action(AuditLog.UPDATED, sale, user=request.user, document_number=sale.reference,
                           note="Price override at POS: " + "; ".join(overrides)[:480])
            log_action(AuditLog.CREATED, sale, user=request.user, document_number=sale.reference,
                       note=f"POS {'sale held' if hold else 'sale completed'} on {session.register.name}, total {total}",
                       new={"total": total, "payments": [{"method": p.method, "amount": p.amount} for p in payments]})

            if customer_id and sale.status == "completed" and sale.total_amount > 0 and system_config.award_points_per_amount:
                points = int(sale.total_amount / system_config.award_points_per_amount) * system_config.award_points_value
                if points > 0:
                    LoyaltyTransaction.objects.create(customer_id=customer_id, order=sale, points=points, type="earn")
                    Customer.objects.filter(pk=customer_id).update(loyalty_points=F("loyalty_points") + points)

            PosCart.objects.filter(register_session=session).delete()   # the cart became a sale (or a held sale)
    except _CartError as exc:
        return _error_response(exc)
    except IntegrityError:
        if token:                                             # two requests with the same token raced: the other one won
            earlier = Sale.objects.filter(checkout_token=token, store=store).select_related("customer").first()
            if earlier is not None:
                return _sale_response(earlier, duplicate=True, user=request.user)
        raise

    # ERP accounting (only does anything when switched on for the company; never blocks the sale)
    if sale.status == "completed" and apps.is_installed("pos_erp"):
        from pos_erp import hooks
        hooks.on_sale_completed(sale, payments)

    return _sale_response(sale, change=change, tendered=tendered if not hold else None, user=request.user)


@login_required
@permission_required("sales.view_sale", raise_exception=True)
def held_sales_api(request):
    """Feeds the POS screen's 'Held sales' window with the branch's actual held sales, newest first."""
    qs = (Sale.objects.filter(store=request.store, status="held").select_related("customer", "biller", "register_session__register")
          .annotate(item_count=Count("items")).order_by("-created_at"))
    sales = [
        {
            "id": s.id, "reference": s.reference,
            "customer_name": s.customer.name if s.customer else "Walk-in customer",
            "total_amount": str(s.total_amount), "item_count": s.item_count,
            "created_at": s.created_at.strftime("%d %b, %H:%M"),
            "cashier": s.biller.get_full_name() or s.biller.username,
            "counter": s.register_session.register.name if s.register_session_id else "",
        }
        for s in qs[:50]
    ]
    return JsonResponse({"sales": sales})


@login_required
@permission_required("sales.add_sale", raise_exception=True)
@require_POST
def resume_held_sale(request, pk):
    """Moves a held sale back into this counter's (server-side) cart and deletes the held record in the same
    transaction, so resuming can never leave a duplicate behind or lose the cart. Held sales never carry stock
    deductions or payments, so deleting is safe - stock and payments are applied once, when the sale is finalized.
    Refuses while this counter's cart still has items (hold or clear it first) so nothing is overwritten."""
    session = get_active_session(request)
    if session is None:
        return JsonResponse({"ok": False, "error": "No register is open."}, status=409)
    with transaction.atomic():
        current = PosCart.objects.select_for_update().filter(register_session=session).first()
        if current is not None and (current.data or {}).get("items"):
            return JsonResponse({"ok": False, "error": "Hold or clear the current cart before resuming another sale.", "code": "cart_busy"}, status=409)
        sale = get_object_or_404(
            Sale.objects.select_for_update().select_related("customer").prefetch_related("items__variant__product__unit"),
            pk=pk, store=request.store, status="held")
        items = [{
            "variant_id": it.variant_id, "name": str(it.variant), "sku": it.variant.sku, "unit": it.variant.product.unit.short_name,
            "unit_price": str(it.unit_price), "list_price": str(it.variant.current_price), "quantity": str(it.quantity),
            "discount_amount": str(it.discount_amount),
        } for it in sale.items.all()]
        customer = {"id": sale.customer_id, "name": sale.customer.name, "phone": sale.customer.phone} if sale.customer_id else None
        cart = clean_cart({"items": items, "customer": customer, "sale_note": sale.sale_note, "staff_note": sale.staff_note, "shipping": sale.shipping_amount})
        cart, notices = revalidate(cart, session.register.warehouse)
        stored, _ = PosCart.objects.get_or_create(register_session=session)
        stored.data, stored.version = cart, stored.version + 1
        stored.save(update_fields=["data", "version", "updated_at"])
        ref = sale.reference
        sale.delete()
        log_action(AuditLog.OTHER, None, user=request.user, document_number=ref, note=f"Held sale {ref} resumed on {session.register.name}")
    return JsonResponse({"ok": True, "cart": cart, "notices": notices, "version": stored.version, "reference": ref})


@login_required
@permission_required("payments.add_payment", raise_exception=True)
@require_POST
def sale_add_payment(request, pk):
    with transaction.atomic():
        sale = get_object_or_404(Sale.objects.select_for_update(), pk=pk, store=request.store)
        method = request.POST.get("method", "cash")
        note = request.POST.get("note", "")[:150]
        try:
            amount = _money(Decimal(request.POST.get("amount")))
            if amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Enter a payment amount greater than zero.")
            return redirect("sales:sale_detail", pk=pk)
        pm = PaymentMethod.active_for(request.store).filter(code=method).first()
        if pm is None:
            messages.error(request, "Unknown or disabled payment method.")
            return redirect("sales:sale_detail", pk=pk)
        reference_no = request.POST.get("reference_no", "").strip()[:100]
        if pm.requires_reference and not reference_no:
            messages.error(request, f"Enter the reference / transaction ID for {pm.name}.")
            return redirect("sales:sale_detail", pk=pk)
        if sale.status in ("held", "cancelled"):
            messages.error(request, f"A {sale.get_status_display().lower()} sale cannot take payments.")
            return redirect("sales:sale_detail", pk=pk)
        if amount > sale.due_amount:
            messages.error(request, f"The balance due is only {sale.due_amount}.")
            return redirect("sales:sale_detail", pk=pk)

        if method == "gift_card":                              # the reference is the card number; spend it for real
            try:
                reference_no = gift_cards.redeem(request.store, reference_no, amount, sale=sale, user=request.user, note=f"Payment on {sale.reference}").code
            except gift_cards.GiftCardError as exc:
                messages.error(request, str(exc))
                return redirect("sales:sale_detail", pk=pk)
        payment = Payment.objects.create(
            sale=sale, reference=NumberingPrefix.next_reference(NumberingPrefix.PAYMENT),
            amount=amount, method=method, note=note, reference_no=reference_no, register_session=get_active_session(request),
            status="completed", received_by=request.user,
        )
    if apps.is_installed("pos_erp"):
        from pos_erp import hooks
        hooks.on_payment_added(payment)
    messages.success(request, "Payment added.")
    return redirect("sales:sale_detail", pk=pk)


# ---------------------------------------------------------------- customer feedback (asked at the POS after payment)
@login_required
@permission_required("sales.add_sale", raise_exception=True)
@require_POST
def pos_feedback(request):
    """The cashier hands the screen to the customer after payment: 1-5 stars and an optional comment. One feedback per sale
    (sending again simply replaces it)."""
    try:
        data = json.loads(request.body or b"{}")
        rating = int(data.get("rating"))
        sale_id = int(data.get("sale_id"))
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Please choose 1 to 5 stars."}, status=400)
    if not 1 <= rating <= 5:
        return JsonResponse({"ok": False, "error": "Please choose 1 to 5 stars."}, status=400)
    comment = str(data.get("comment") or "").strip()[:1000]
    sale = get_object_or_404(Sale, pk=sale_id, store=request.store)
    SaleFeedback.objects.update_or_create(sale=sale, defaults={"rating": rating, "comment": comment, "created_by": request.user})
    return JsonResponse({"ok": True})


@login_required
def sale_feedback(request, pk):
    """Side drawer on a sale: record (or correct) what the customer said about this visit - for sales where the POS did not ask."""
    if not request.user.has_perm("sales.add_sale"):
        raise PermissionDenied
    sale = get_object_or_404(Sale, pk=pk, store=request.store)
    if request.method == "GET" and request.headers.get("x-requested-with") != "XMLHttpRequest":
        return redirect("sales:sale_detail", pk=sale.pk)
    if sale.status in ("held", "cancelled"):
        raise PermissionDenied
    feedback = SaleFeedback.objects.filter(sale=sale).first()
    error = ""
    if request.method == "POST":
        try:
            rating = int(request.POST.get("rating", ""))
        except ValueError:
            rating = 0
        comment = request.POST.get("comment", "").strip()[:1000]
        if not 1 <= rating <= 5:
            error = "Choose 1 to 5 stars."
        else:
            SaleFeedback.objects.update_or_create(sale=sale, defaults={"rating": rating, "comment": comment, "created_by": request.user})
            return JsonResponse({"ok": True, "message": f"Feedback saved for {sale.reference}."})
    if request.method == "POST":
        rating, comment = request.POST.get("rating", "") or 0, request.POST.get("comment", "")
    else:
        rating, comment = (feedback.rating, feedback.comment) if feedback else (0, "")
    return render(request, "sales/_sale_feedback.html", {"sale": sale, "error": error, "rating": rating, "comment": comment})


class FeedbackListView(PagedListMixin, LoginRequiredMixin, ListView):
    """What customers said, newest first, with the average and a 1-5 star breakdown for the current filter."""
    model = SaleFeedback
    template_name = "sales/feedback_list.html"
    context_object_name = "rows"
    filter_params = ("q", "rating", "df", "dt")

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm("sales.view_sale"):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = SaleFeedback.objects.filter(sale__store=self.request.store).select_related("sale", "sale__customer", "created_by")
        g = self.request.GET
        if g.get("rating", "").isdigit():
            qs = qs.filter(rating=int(g["rating"]))
        if g.get("df"):
            qs = qs.filter(created_at__date__gte=g["df"])
        if g.get("dt"):
            qs = qs.filter(created_at__date__lte=g["dt"])
        if g.get("q"):
            qs = qs.filter(Q(comment__icontains=g["q"]) | Q(sale__reference__icontains=g["q"]) | Q(sale__customer__name__icontains=g["q"]))
        return qs.order_by("-created_at")

    def get_context_data(self, **kwargs):
        from django.db.models import Avg
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        counts = {r["rating"]: r["n"] for r in qs.order_by().values("rating").annotate(n=Count("id"))}
        total = sum(counts.values())
        avg = qs.aggregate(a=Avg("rating"))["a"]
        ctx.update({
            "avg_rating": f"{avg:.1f}" if avg else "—", "avg_stars": int(round(avg)) if avg else 0, "total_feedback": total,
            "breakdown": [{"stars": n, "count": counts.get(n, 0), "pct": round(100 * counts.get(n, 0) / total) if total else 0} for n in (5, 4, 3, 2, 1)],
            "df": self.request.GET.get("df", ""), "dt": self.request.GET.get("dt", ""), "star_range": range(1, 6),
        })
        return ctx
