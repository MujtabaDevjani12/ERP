"""Accounting entries for stock that enters or leaves OUTSIDE the ERP procurement / POS flows.

`post_stock_movement` never touches the general ledger ("callers post their own journals"), so the product form's opening stock and the
older Suppliers > Purchase screens used to put goods on the shelf without any entry: the Inventory report (stock x cost) and the
Balance sheet Inventory line drifted apart. These helpers post the matching entries; they never block the stock change itself
(a missing chart of accounts / closed period is logged and can be fixed with `python manage.py backfill_stock_gl`).
"""
import logging
from decimal import Decimal

from django.db import transaction

from accounting.services import create_journal_entry
from company.utils import company_of_store
from core.money import q2
from .services import inventory_account_key

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
METHOD_ACCOUNT = {"cash": "cash", "card": "bank", "bank_transfer": "bank", "cheque": "bank"}


def _post(store, *, source, narration, lines, date=None, user=None, voucher_type=None):
    company = company_of_store(store)
    if company is None:
        return None
    try:
        with transaction.atomic():
            kwargs = {"voucher_type": voucher_type} if voucher_type else {}
            return create_journal_entry(company, date=date, narration=narration, source=source, store=store, user=user, lines=lines, **kwargs)
    except Exception as exc:                                  # noqa: BLE001 - the stock change must still go through
        logger.warning("Stock GL posting failed for %s: %s", source, exc)
        return None


def post_opening_stock(warehouse, variant, quantity, unit_cost, *, reference_no="", user=None, date=None):
    """Dr Inventory / Cr Opening balance equity for goods that were already on the shelf when the product was created."""
    value = q2(Decimal(quantity) * Decimal(unit_cost or 0))
    if value <= 0:
        return None
    return _post(warehouse.store, source=("opening_stock", f"{variant.pk}:{warehouse.pk}:{reference_no}"), narration=f"Opening stock {reference_no or variant}",
                 date=date, user=user, voucher_type="OPENING",
                 lines=[{"account": inventory_account_key(warehouse), "debit": value}, {"account": "opening_balance_equity", "credit": value}])


def post_legacy_receipt(order, user=None):
    """Received purchase order from Suppliers > Purchases: Dr Inventory / Cr Accounts payable."""
    value = q2(sum((i.quantity * i.cost_price for i in order.items.all()), ZERO))
    if value <= 0:
        return None
    return _post(order.warehouse.store, source=("po_legacy", order.pk), narration=f"Purchase {order.reference}", date=order.order_date, user=user,
                 lines=[{"account": inventory_account_key(order.warehouse), "debit": value},
                        {"account": "ap", "credit": value, "party_type": "supplier", "party_id": order.supplier_id, "description": order.reference}])


def post_legacy_payment(payment, user=None):
    """Money paid to the supplier: Dr Accounts payable / Cr Cash or Bank."""
    order = payment.purchase_order
    amount = q2(payment.amount)
    if amount <= 0:
        return None
    return _post(order.warehouse.store, source=("po_legacy_payment", payment.pk), narration=f"Payment {payment.reference} for {order.reference}",
                 date=payment.paid_on, user=user, voucher_type="PV",
                 lines=[{"account": "ap", "debit": amount, "party_type": "supplier", "party_id": order.supplier_id},
                        {"account": METHOD_ACCOUNT.get(payment.method, "bank"), "credit": amount}])


def post_legacy_return(purchase_return, user=None):
    """Goods sent back to the supplier: Dr Accounts payable / Cr Inventory."""
    order = purchase_return.purchase_order
    value = q2(sum((i.quantity * i.cost_price for i in purchase_return.items.all()), ZERO))
    if value <= 0:
        return None
    return _post(order.warehouse.store, source=("po_legacy_return", purchase_return.pk), narration=f"Purchase return {purchase_return.reference}",
                 date=None, user=user, voucher_type="DEBIT_NOTE",
                 lines=[{"account": "ap", "debit": value, "party_type": "supplier", "party_id": order.supplier_id},
                        {"account": inventory_account_key(order.warehouse), "credit": value}])
