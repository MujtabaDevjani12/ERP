"""Gift card balance rules. All money movement goes through here so every change is locked, checked and written to the card's history."""
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import GiftCard, GiftCardTransaction

TWO = Decimal("0.01")


class GiftCardError(Exception):
    """A rule about the card was broken; the message is safe to show the cashier."""


def money(value):
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def _clean_code(code):
    return str(code or "").strip().upper()[:30]


def find(store, code, *, lock=False):
    """The usable card for this code in this branch, or a GiftCardError saying why not."""
    code = _clean_code(code)
    if not code:
        raise GiftCardError("Enter the gift card number.")
    qs = GiftCard.objects.select_for_update() if lock else GiftCard.objects
    card = qs.filter(store=store, code__iexact=code).first()
    if card is None:
        raise GiftCardError("No gift card with this number.")
    if not card.is_active:
        raise GiftCardError("This gift card is switched off.")
    if card.expires_on and card.expires_on < timezone.localdate():
        raise GiftCardError(f"This gift card expired on {card.expires_on:%d %b %Y}.")
    return card


@transaction.atomic
def redeem(store, code, amount, *, sale=None, user=None, note=""):
    card = find(store, code, lock=True)
    amount = money(amount)
    if amount <= 0:
        raise GiftCardError("The amount must be more than zero.")
    if card.current_balance < amount:
        raise GiftCardError(f"Gift card {card.code} only has {card.current_balance} left, but {amount} was asked for.")
    card.current_balance -= amount
    card.save(update_fields=["current_balance", "updated_at"])
    GiftCardTransaction.objects.create(card=card, kind=GiftCardTransaction.REDEEM, amount=-amount, balance_after=card.current_balance, sale=sale, user=user, note=note[:200])
    return card


@transaction.atomic
def top_up(card_id, amount, *, user=None, note="", kind=GiftCardTransaction.TOPUP):
    card = GiftCard.objects.select_for_update().get(pk=card_id)
    amount = money(amount)
    if amount <= 0:
        raise GiftCardError("The top-up amount must be more than zero.")
    card.current_balance += amount
    card.save(update_fields=["current_balance", "updated_at"])
    GiftCardTransaction.objects.create(card=card, kind=kind, amount=amount, balance_after=card.current_balance, user=user, note=note[:200])
    return card


DEPOSIT_PREFIX = "DEP-"


def deposit_code(customer):
    return f"{DEPOSIT_PREFIX}{customer.pk}"


@transaction.atomic
def add_deposit(customer, amount, *, user=None, note=""):
    """Money a customer leaves with the shop in advance. It lives on a prepaid card of its own (DEP-<customer id>), so it is spent
    at the POS like any gift card and every movement is in the card's history."""
    amount = money(amount)
    if amount <= 0:
        raise GiftCardError("The deposit must be more than zero.")
    card, created = GiftCard.objects.select_for_update().get_or_create(
        code=deposit_code(customer), defaults={"store": customer.store, "customer": customer, "initial_balance": 0, "current_balance": 0})
    if card.store_id != customer.store_id:
        raise GiftCardError("This deposit card belongs to another branch.")
    if not card.is_active:
        raise GiftCardError("This customer's deposit card is switched off.")
    return top_up(card.pk, amount, user=user, note=note or "Customer deposit", kind=GiftCardTransaction.ISSUE if created else GiftCardTransaction.TOPUP)
