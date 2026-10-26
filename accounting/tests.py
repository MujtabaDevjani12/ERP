from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounting import assets, bank, reports
from accounting.models import (Account, BankAccount, BankStatementLine, Budget, BudgetLine, FixedAsset, JournalEntry,
                               JournalLine)
from accounting.services import create_journal_entry, reverse_entry, set_period_lock
from company.models import Company
from core.errors import ERPError, UnbalancedJournal


def money(v):
    return Decimal(str(v))


class AccountingBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme", fiscal_year_start_month=1)
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.d = date(2026, 3, 10)

    def post(self, lines, **kw):
        return create_journal_entry(self.company, lines=lines, date=kw.pop("date", self.d), **kw)


class JournalRulesTests(AccountingBase):
    def test_balanced_entry_posts_and_gets_a_number(self):
        e = self.post([{"account": "cash", "debit": 100}, {"account": "sales_retail", "credit": 100}])
        self.assertEqual(e.status, "posted")
        self.assertTrue(e.entry_number.startswith("SJ-2026-"))
        self.assertEqual(e.total_debit, e.total_credit)

    def test_unbalanced_entry_is_rejected_and_nothing_is_saved(self):
        with self.assertRaises(UnbalancedJournal):
            self.post([{"account": "cash", "debit": 100}, {"account": "sales_retail", "credit": 99}])
        self.assertEqual(JournalEntry.objects.count(), 0)
        self.assertEqual(JournalLine.objects.count(), 0)

    def test_single_line_and_two_sided_line_rejected(self):
        with self.assertRaises(UnbalancedJournal):
            self.post([{"account": "cash", "debit": 100}])
        with self.assertRaises(ERPError):
            self.post([{"account": "cash", "debit": 5, "credit": 5}, {"account": "ap", "credit": 1}])

    def test_group_account_rejected(self):
        group = Account.objects.get(company=self.company, code="1000")
        with self.assertRaises(ERPError):
            self.post([{"account": group, "debit": 10}, {"account": "cash", "credit": 10}])

    def test_posting_same_source_twice_is_idempotent(self):
        lines = [{"account": "cash", "debit": 50}, {"account": "sales_retail", "credit": 50}]
        a = self.post(lines, source=("pos_sale", 7))
        b = self.post(lines, source=("pos_sale", 7))
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(JournalEntry.objects.count(), 1)

    def test_posted_entry_cannot_be_edited_or_deleted(self):
        e = self.post([{"account": "cash", "debit": 10}, {"account": "sales_retail", "credit": 10}])
        e.narration = "tampered"
        with self.assertRaises(ERPError):
            e.save()
        with self.assertRaises(ERPError):
            e.delete()
        with self.assertRaises(ERPError):
            JournalLine.objects.create(entry=e, account=Account.objects.get(company=self.company, system_key="cash"), debit=1)

    def test_db_rejects_negative_or_two_sided_lines(self):
        e = JournalEntry.objects.create(company=self.company, date=self.d)
        cash = Account.objects.get(company=self.company, system_key="cash")
        with self.assertRaises(IntegrityError), transaction.atomic():
            JournalLine.objects.create(entry=e, account=cash, debit=5, credit=5)
        with self.assertRaises(IntegrityError), transaction.atomic():
            JournalLine.objects.create(entry=e, account=cash, debit=-5)

    def test_reversal_nets_to_zero_and_original_is_marked(self):
        e = self.post([{"account": "cash", "debit": 100}, {"account": "sales_retail", "credit": 100}])
        r = reverse_entry(e, reason="mistake", date=self.d)
        e.refresh_from_db()
        self.assertEqual(e.status, "reversed")
        self.assertEqual(r.reversal_of_id, e.pk)
        tb = reports.trial_balance(self.company, as_of=date(2026, 12, 31))
        self.assertEqual(tb["rows"], [])             # everything cancelled
        with self.assertRaises(ERPError):
            reverse_entry(e)                          # cannot reverse twice

    def test_period_lock_blocks_old_dates(self):
        set_period_lock(self.company, date(2026, 3, 31))
        with self.assertRaises(ERPError):
            self.post([{"account": "cash", "debit": 1}, {"account": "sales_retail", "credit": 1}])
        self.post([{"account": "cash", "debit": 1}, {"account": "sales_retail", "credit": 1}], date=date(2026, 4, 1))

    def test_reverse_requires_permission_for_a_normal_user(self):
        user = get_user_model().objects.create_user("clerk", "c@example.com", "pw-12345-x")
        e = self.post([{"account": "cash", "debit": 1}, {"account": "sales_retail", "credit": 1}])
        with self.assertRaises(PermissionDenied):
            reverse_entry(e, user=user)


class ReportTests(AccountingBase):
    def setUp(self):
        super().setUp()
        self.post([{"account": "cash", "debit": 1000}, {"account": "capital", "credit": 1000}], date=date(2026, 1, 1))
        self.post([{"account": "inventory", "debit": 400}, {"account": "ap", "credit": 400}], date=date(2026, 1, 5))
        self.post([{"account": "ar", "debit": 600}, {"account": "sales_wholesale", "credit": 600}], date=date(2026, 2, 1))
        self.post([{"account": "cogs", "debit": 250}, {"account": "inventory", "credit": 250}], date=date(2026, 2, 1))
        self.post([{"account": "rent", "debit": 100}, {"account": "cash", "credit": 100}], date=date(2026, 2, 2))

    def test_trial_balance_is_balanced(self):
        tb = reports.trial_balance(self.company, as_of=date(2026, 12, 31))
        self.assertTrue(tb["balanced"])
        self.assertEqual(tb["total_debit"], money("2000.00"))

    def test_profit_and_loss(self):
        pl = reports.profit_and_loss(self.company, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
        self.assertEqual(pl["total_revenue"], money("600.00"))
        self.assertEqual(pl["cogs"], money("250.00"))
        self.assertEqual(pl["gross_profit"], money("350.00"))
        self.assertEqual(pl["net_profit"], money("250.00"))

    def test_balance_sheet_balances(self):
        bs = reports.balance_sheet(self.company, as_of=date(2026, 12, 31))
        self.assertTrue(bs["balanced"])
        self.assertEqual(bs["total_assets"], money("1650.00"))   # cash 900 + AR 600 + inventory 150

    def test_general_ledger_running_balance(self):
        cash = Account.objects.get(company=self.company, system_key="cash")
        gl = reports.general_ledger(self.company, cash, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
        self.assertEqual(gl["closing"], money("900.00"))
        self.assertEqual([r["balance"] for r in gl["rows"]], [money("1000.00"), money("900.00")])

    def test_cash_flow_classifies_movements(self):
        cf = reports.cash_flow(self.company, date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
        self.assertEqual(cf["financing"], money("1000.00"))      # capital introduced
        self.assertEqual(cf["operating"], money("-100.00"))      # rent paid
        self.assertEqual(cf["closing_cash"], money("900.00"))

    def test_aging_buckets(self):
        items = [
            {"party": "A", "party_id": 1, "document_number": "I1", "invoice_date": date(2026, 1, 1), "due_date": date(2026, 1, 31), "outstanding": 100},
            {"party": "A", "party_id": 1, "document_number": "I2", "invoice_date": date(2026, 1, 1), "due_date": date(2026, 3, 1), "outstanding": 50},
            {"party": "B", "party_id": 2, "document_number": "I3", "invoice_date": date(2025, 6, 1), "due_date": date(2025, 7, 1), "outstanding": 70},
            {"party": "B", "party_id": 2, "document_number": "I4", "invoice_date": date(2026, 1, 1), "due_date": date(2026, 1, 1), "outstanding": 0},
        ]
        ag = reports.aging_buckets(items, as_of=date(2026, 4, 2))
        self.assertEqual(ag["summary"]["61-90"], money("100.00"))   # due 31 Jan -> 61 days on 2 Apr
        self.assertEqual(ag["summary"]["31-60"], money("50.00"))    # due 1 Mar  -> 32 days
        self.assertEqual(ag["summary"]["90+"], money("70.00"))
        self.assertEqual(ag["total"], money("220.00"))              # zero-outstanding invoice ignored
        self.assertEqual(ag["by_party"]["A"]["total"], money("150.00"))


class BankTests(AccountingBase):
    def setUp(self):
        super().setUp()
        ledger = Account.objects.get(company=self.company, system_key="bank")
        self.bank = BankAccount.objects.create(company=self.company, name="HBL", ledger_account=ledger)
        self.post([{"account": "bank", "debit": 500}, {"account": "capital", "credit": 500}], date=date(2026, 3, 1))
        self.post([{"account": "rent", "debit": 200}, {"account": "bank", "credit": 200}], date=date(2026, 3, 5))

    def test_auto_match_and_reconciliation(self):
        BankStatementLine.objects.create(bank_account=self.bank, date=date(2026, 3, 2), amount=500, reference="DEP1")
        BankStatementLine.objects.create(bank_account=self.bank, date=date(2026, 3, 6), amount=-200, reference="CHQ1")
        BankStatementLine.objects.create(bank_account=self.bank, date=date(2026, 3, 7), amount=-33, reference="FEE")
        self.assertEqual(bank.auto_match(self.bank, tolerance_days=3), 2)
        rep = bank.reconciliation_report(self.bank, as_of=date(2026, 3, 31))
        self.assertEqual(rep["book_balance"], money("300.00"))
        self.assertEqual(rep["statement_balance"], money("267.00"))
        self.assertEqual(len(rep["unmatched_statement_lines"]), 1)    # the bank fee
        self.assertEqual(len(rep["unmatched_book_lines"]), 0)

    def test_match_rejects_wrong_amount(self):
        s = BankStatementLine.objects.create(bank_account=self.bank, date=date(2026, 3, 2), amount=499)
        line = JournalLine.objects.get(account=self.bank.ledger_account, debit=500)
        with self.assertRaises(ERPError):
            bank.match(s, line)


class AssetAndBudgetTests(AccountingBase):
    def test_straight_line_depreciation_and_rerun_is_safe(self):
        asset = FixedAsset.objects.create(company=self.company, code="A1", name="Machine", purchase_date=date(2026, 1, 15),
                                          purchase_cost=1200, useful_life_months=12, residual_value=0)
        created = assets.run_depreciation(self.company, through=date(2026, 3, 31))
        self.assertEqual(len(created), 2)             # Feb, Mar (starts the month after purchase)
        self.assertEqual(created[0].amount, money("100.00"))
        asset.refresh_from_db()
        self.assertEqual(asset.accumulated_depreciation, money("200.00"))
        self.assertEqual(assets.run_depreciation(self.company, through=date(2026, 3, 31)), [])
        acc = Account.objects.get(company=self.company, system_key="accumulated_depreciation")
        self.assertEqual(reports.balance_of(self.company, acc, as_of=date(2026, 12, 31)), money("-200.00"))   # contra-asset: credit balance

    def test_depreciation_stops_at_residual_value(self):
        FixedAsset.objects.create(company=self.company, code="A2", name="Laptop", purchase_date=date(2026, 1, 1),
                                  purchase_cost=1000, useful_life_months=3, residual_value=100)
        assets.run_depreciation(self.company, through=date(2026, 12, 31))
        a = FixedAsset.objects.get(code="A2")
        self.assertEqual(a.accumulated_depreciation, money("900.00"))
        self.assertEqual(a.current_value, money("100.00"))

    def test_actual_vs_budget(self):
        from accounting.budgets import actual_vs_budget
        rent = Account.objects.get(company=self.company, system_key="rent")
        budget = Budget.objects.create(company=self.company, name="Opex", fiscal_year=2026)
        BudgetLine.objects.create(budget=budget, account=rent, month_index=3, amount=300)
        self.post([{"account": "rent", "debit": 250}, {"account": "cash", "credit": 250}], date=date(2026, 3, 15))
        res = actual_vs_budget(budget)
        self.assertEqual(res["rows"][0]["actual"], money("250.00"))
        self.assertEqual(res["rows"][0]["variance"], money("50.00"))     # under budget on an expense = favourable
