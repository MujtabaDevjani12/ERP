"""
Fixed-asset depreciation (monthly), posted automatically as
    Dr Depreciation Expense / Cr Accumulated Depreciation
`run_depreciation(company, through)` is safe to run again: each asset/month
is booked once (unique constraint) and months already booked are skipped.
"""
import calendar
from datetime import date

from django.db import transaction

from core.errors import ERPError
from core.money import ZERO, q2
from .models import DepreciationEntry, FixedAsset
from .services import create_journal_entry


def _month_end(year, month):
    return date(year, month, calendar.monthrange(year, month)[1])


def _next_period_end(asset):
    if asset.last_depreciated_through:
        y, m = asset.last_depreciated_through.year, asset.last_depreciated_through.month
        m += 1
        if m == 13:
            y, m = y + 1, 1
        return _month_end(y, m)
    # first month = month AFTER purchase month (depreciation starts the next month)
    y, m = asset.purchase_date.year, asset.purchase_date.month + 1
    if m == 13:
        y, m = y + 1, 1
    return _month_end(y, m)


def monthly_amount(asset) -> "Decimal":
    depreciable_left = asset.purchase_cost - asset.residual_value - asset.accumulated_depreciation
    if depreciable_left <= 0:
        return ZERO
    if asset.method == FixedAsset.SL:
        amount = (asset.purchase_cost - asset.residual_value) / asset.useful_life_months
    else:
        book_value = asset.purchase_cost - asset.accumulated_depreciation
        amount = book_value * (asset.declining_rate_percent / 100) / 12
        floor = book_value - asset.residual_value
        amount = min(amount, floor)
    return q2(min(amount, depreciable_left))


@transaction.atomic
def run_depreciation(company, through: date, user=None):
    """Book all outstanding months up to `through` for every active asset.
    Returns the list of DepreciationEntry rows created."""
    from .services import get_account
    created = []
    expense = get_account(company, "depreciation_expense")
    accumulated = get_account(company, "accumulated_depreciation")
    for asset in FixedAsset.objects.select_for_update().filter(company=company, status="active"):
        while True:
            period_end = _next_period_end(asset)
            if period_end > through:
                break
            amount = monthly_amount(asset)
            asset.last_depreciated_through = period_end
            if amount <= 0:
                asset.save(update_fields=["last_depreciated_through", "updated_at"])
                if asset.accumulated_depreciation >= asset.purchase_cost - asset.residual_value:
                    break
                continue
            entry = create_journal_entry(
                company, voucher_type="SYSTEM", date=period_end, narration=f"Depreciation {asset.code} {period_end:%b %Y}",
                source=("depreciation", f"{asset.pk}:{period_end:%Y%m}"), user=user,
                lines=[
                    {"account": expense, "debit": amount, "cost_center": asset.cost_center, "description": asset.name},
                    {"account": accumulated, "credit": amount, "description": asset.name},
                ],
            )
            asset.accumulated_depreciation += amount
            asset.save(update_fields=["accumulated_depreciation", "last_depreciated_through", "updated_at"])
            created.append(DepreciationEntry.objects.create(asset=asset, period_end=period_end, amount=amount, journal_entry=entry))
    return created


@transaction.atomic
def record_asset_purchase(asset, *, credit_account_key="bank", user=None):
    """Optional helper: Dr Fixed Assets / Cr Bank (or ap, cash...) for the purchase cost."""
    from .services import get_account
    company = asset.company
    return create_journal_entry(
        company, voucher_type="SYSTEM", date=asset.purchase_date, narration=f"Purchase of asset {asset.code}",
        source=("asset_purchase", asset.pk), user=user,
        lines=[
            {"account": get_account(company, "fixed_assets"), "debit": asset.purchase_cost, "description": asset.name},
            {"account": get_account(company, credit_account_key), "credit": asset.purchase_cost},
        ],
    )
