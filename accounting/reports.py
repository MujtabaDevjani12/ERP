"""
Financial reports computed straight from posted journal lines.
Entries with status 'posted' or 'reversed' both count (a reversed entry is
cancelled out by its reversal entry); drafts never count.
All functions return plain dicts/lists so they can feed HTML, Excel, PDF or API.
"""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q, Sum

from core.money import ZERO, q2
from .models import Account, JournalLine, POSTED_STATUSES


def _lines(company, *, date_from=None, date_to=None, store=None, cost_center=None):
    qs = JournalLine.objects.filter(entry__company=company, entry__status__in=POSTED_STATUSES)
    if date_from:
        qs = qs.filter(entry__date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__date__lte=date_to)
    if store is not None:
        qs = qs.filter(entry__store=store)
    if cost_center is not None:
        qs = qs.filter(cost_center=cost_center)
    return qs


def account_totals(company, **filters):
    """{account_id: (debit_total, credit_total)}"""
    rows = _lines(company, **filters).values("account_id").annotate(d=Sum("debit"), c=Sum("credit"))
    return {r["account_id"]: (r["d"] or ZERO, r["c"] or ZERO) for r in rows}


def natural_balance(account: Account, debit, credit):
    return (debit - credit) if account.normal_balance == "debit" else (credit - debit)


def balance_of(company, system_key_or_account, *, as_of=None, date_from=None, **filters):
    account = system_key_or_account
    if not isinstance(account, Account):
        account = Account.objects.get(company=company, system_key=account)
    qs = _lines(company, date_from=date_from, date_to=as_of, **filters).filter(account=account)
    agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    return natural_balance(account, agg["d"] or ZERO, agg["c"] or ZERO)


# ---------------------------------------------------------------- trial balance
def trial_balance(company, *, as_of=None, date_from=None, store=None, cost_center=None):
    totals = account_totals(company, date_from=date_from, date_to=as_of, store=store, cost_center=cost_center)
    rows, total_d, total_c = [], ZERO, ZERO
    for account in Account.objects.filter(company=company, is_group=False).order_by("code"):
        d, c = totals.get(account.id, (ZERO, ZERO))
        net = d - c
        if net == 0:
            continue
        debit_col = net if net > 0 else ZERO
        credit_col = -net if net < 0 else ZERO
        rows.append({"code": account.code, "name": account.name, "type": account.account_type,
                     "debit": q2(debit_col), "credit": q2(credit_col)})
        total_d += debit_col
        total_c += credit_col
    return {"rows": rows, "total_debit": q2(total_d), "total_credit": q2(total_c),
            "balanced": q2(total_d) == q2(total_c)}


# ---------------------------------------------------------------- general ledger
def general_ledger(company, account: Account, *, date_from=None, date_to=None):
    opening_agg = JournalLine.objects.filter(
        entry__company=company, entry__status__in=POSTED_STATUSES, account=account,
        entry__date__lt=date_from if date_from else date(1900, 1, 1),
    ).aggregate(d=Sum("debit"), c=Sum("credit"))
    opening = natural_balance(account, opening_agg["d"] or ZERO, opening_agg["c"] or ZERO) if date_from else ZERO

    qs = _lines(company, date_from=date_from, date_to=date_to).filter(account=account).select_related("entry").order_by(
        "entry__date", "entry_id", "id")
    running = opening
    rows = []
    for l in qs:
        running += natural_balance(account, l.debit, l.credit)
        rows.append({"date": l.entry.date, "entry_number": l.entry.entry_number, "voucher_type": l.entry.voucher_type,
                     "narration": l.description or l.entry.narration, "debit": l.debit, "credit": l.credit,
                     "balance": q2(running)})
    return {"account": account, "opening": q2(opening), "rows": rows, "closing": q2(running)}


# ---------------------------------------------------------------- P&L
def profit_and_loss(company, *, date_from, date_to, store=None, cost_center=None):
    totals = account_totals(company, date_from=date_from, date_to=date_to, store=store, cost_center=cost_center)
    revenue, expenses = [], []
    total_rev = total_exp = cogs = ZERO
    for account in Account.objects.filter(company=company, is_group=False,
                                          account_type__in=[Account.REVENUE, Account.EXPENSE]).order_by("code"):
        d, c = totals.get(account.id, (ZERO, ZERO))
        amount = natural_balance(account, d, c)
        if d == 0 and c == 0:
            continue
        row = {"code": account.code, "name": account.name, "amount": q2(amount)}
        if account.account_type == Account.REVENUE:
            revenue.append(row)
            total_rev += amount
        else:
            expenses.append(row)
            total_exp += amount
            if account.system_key == "cogs":
                cogs += amount
    return {
        "revenue": revenue, "expenses": expenses,
        "total_revenue": q2(total_rev), "total_expenses": q2(total_exp), "cogs": q2(cogs),
        "gross_profit": q2(total_rev - cogs), "net_profit": q2(total_rev - total_exp),
    }


# ---------------------------------------------------------------- balance sheet
def balance_sheet(company, *, as_of):
    totals = account_totals(company, date_to=as_of)
    sections = {Account.ASSET: [], Account.LIABILITY: [], Account.EQUITY: []}
    sums = defaultdict(lambda: ZERO)
    earnings = ZERO
    for account in Account.objects.filter(company=company, is_group=False).order_by("code"):
        d, c = totals.get(account.id, (ZERO, ZERO))
        if d == 0 and c == 0:
            continue
        amount = natural_balance(account, d, c)
        if account.account_type in sections:
            sections[account.account_type].append({"code": account.code, "name": account.name, "amount": q2(amount)})
            sums[account.account_type] += amount
        elif account.account_type == Account.REVENUE:
            earnings += amount
        elif account.account_type == Account.EXPENSE:
            earnings -= amount
    sections[Account.EQUITY].append({"code": "", "name": "Current period earnings (not yet closed)", "amount": q2(earnings)})
    sums[Account.EQUITY] += earnings
    total_assets, total_liab, total_equity = sums[Account.ASSET], sums[Account.LIABILITY], sums[Account.EQUITY]
    return {
        "assets": sections[Account.ASSET], "liabilities": sections[Account.LIABILITY], "equity": sections[Account.EQUITY],
        "total_assets": q2(total_assets), "total_liabilities": q2(total_liab), "total_equity": q2(total_equity),
        "balanced": q2(total_assets) == q2(total_liab + total_equity),
    }


# ---------------------------------------------------------------- cash flow
def _cash_account_ids(company):
    from .models import BankAccount
    ids = set(Account.objects.filter(company=company, system_key__in=["cash", "bank"]).values_list("id", flat=True))
    ids |= set(BankAccount.objects.filter(company=company).values_list("ledger_account_id", flat=True))
    return ids


def _cash_class(account: Account) -> str:
    if account.system_key == "fixed_assets":
        return "investing"
    if account.account_type == Account.EQUITY or account.system_key == "loans_payable":
        return "financing"
    return "operating"


def cash_flow(company, *, date_from, date_to):
    """Direct-method cash flow: every movement on a cash/bank account is
    classified by the OTHER side of its journal entry (operating / investing /
    financing). Opening and closing cash come from the ledger."""
    cash_ids = _cash_account_ids(company)
    opening = ZERO
    if cash_ids:
        agg = JournalLine.objects.filter(entry__company=company, entry__status__in=POSTED_STATUSES,
                                         account_id__in=cash_ids, entry__date__lt=date_from).aggregate(d=Sum("debit"), c=Sum("credit"))
        opening = (agg["d"] or ZERO) - (agg["c"] or ZERO)

    buckets = {"operating": ZERO, "investing": ZERO, "financing": ZERO}
    entries = JournalLine.objects.filter(
        entry__company=company, entry__status__in=POSTED_STATUSES, entry__date__gte=date_from, entry__date__lte=date_to,
        account_id__in=cash_ids,
    ).values_list("entry_id", flat=True).distinct()
    for entry_id in entries:
        lines = list(JournalLine.objects.filter(entry_id=entry_id).select_related("account"))
        cash_net = sum((l.debit - l.credit for l in lines if l.account_id in cash_ids), ZERO)
        others = [l for l in lines if l.account_id not in cash_ids]
        weight_total = sum((l.debit + l.credit for l in others), ZERO)
        if cash_net == 0 or weight_total == 0:
            continue
        for l in others:
            share = (l.debit + l.credit) / weight_total
            buckets[_cash_class(l.account)] += cash_net * share
    net_change = sum(buckets.values(), ZERO)
    return {
        "opening_cash": q2(opening), "operating": q2(buckets["operating"]), "investing": q2(buckets["investing"]),
        "financing": q2(buckets["financing"]), "net_change": q2(net_change), "closing_cash": q2(opening + net_change),
    }


# ---------------------------------------------------------------- tax
def tax_report(company, *, date_from, date_to):
    def bal(key):
        try:
            return balance_of(company, key, date_from=date_from, as_of=date_to)
        except Account.DoesNotExist:
            return ZERO
    output_tax, input_tax = bal("output_tax"), bal("input_tax")
    return {"output_tax": q2(output_tax), "input_tax": q2(input_tax), "net_payable": q2(output_tax - input_tax)}


# ---------------------------------------------------------------- aging
BUCKETS = [("0-30", 0, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10 ** 6)]


def aging_buckets(items, *, as_of=None, basis="due_date"):
    """items: iterable of dicts with party, party_id, document_number,
    due_date, invoice_date, outstanding. `basis` = which date the age is
    measured from. Not-yet-due items count in the first bucket."""
    as_of = as_of or date.today()
    summary = {name: ZERO for name, _, _ in BUCKETS}
    by_party = {}
    detail = []
    for item in items:
        outstanding = Decimal(item["outstanding"])
        if outstanding <= 0:
            continue
        ref_date = item["due_date"] if basis == "due_date" else item["invoice_date"]
        days = max((as_of - ref_date).days, 0)
        bucket = next(name for name, lo, hi in BUCKETS if lo <= days <= hi)
        summary[bucket] += outstanding
        party = by_party.setdefault(item["party"], {name: ZERO for name, _, _ in BUCKETS} | {"total": ZERO})
        party[bucket] += outstanding
        party["total"] += outstanding
        detail.append({**item, "days": days, "bucket": bucket})
    return {"summary": {k: q2(v) for k, v in summary.items()}, "total": q2(sum(summary.values(), ZERO)),
            "by_party": {p: {k: q2(v) for k, v in row.items()} for p, row in by_party.items()}, "detail": detail}
