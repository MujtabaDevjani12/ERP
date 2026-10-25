"""Cheque register, fiscal years, recurring journals and the new accounting reports/screens."""
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounting import cheques, periods
from accounting.models import (Account, AccountingPeriodLock, BankAccount, Cheque, FiscalYear, JournalEntry, RecurringJournal,
                               RecurringJournalLine)
from accounting.reports import balance_of
from accounting.services import create_journal_entry
from company.models import Company
from core.errors import ERPError
from tenants.models import Store


class AdditionsBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme", fiscal_year_start_month=1)
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.boss = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-x")
        self.bank_ledger = Account.objects.get(company=self.company, system_key="bank")
        self.ap = Account.objects.get(company=self.company, system_key="ap")
        self.ar = Account.objects.get(company=self.company, system_key="ar")
        self.bank = BankAccount.objects.create(company=self.company, name="HBL Current", ledger_account=self.bank_ledger)
        # some money in the bank so cheques have something to move
        create_journal_entry(self.company, date=date(2026, 1, 5), lines=[{"account": self.bank_ledger, "debit": 100000}, {"account": "sales_retail", "credit": 100000}])


class ChequeTests(AdditionsBase):
    def cheque(self, kind="issued", amount="5000", number="000101", **kw):
        return Cheque.objects.create(company=self.company, cheque_type=kind, bank_account=self.bank, cheque_number=number, party_name="Vendor Ltd",
                                     cheque_date=date(2026, 2, 1), amount=Decimal(amount), counter_account=self.ap if kind == "issued" else self.ar, **kw)

    def test_clearing_an_issued_cheque_posts_payment_voucher(self):
        c = self.cheque()
        before = balance_of(self.company, self.bank_ledger)
        cheques.clear_cheque(c, on=date(2026, 2, 3), user=self.boss)
        c.refresh_from_db()
        self.assertEqual((c.status, c.cleared_on), ("cleared", date(2026, 2, 3)))
        self.assertEqual(c.journal_entry.voucher_type, "PV")
        self.assertEqual(balance_of(self.company, self.bank_ledger), before - Decimal("5000"))

    def test_clearing_a_received_cheque_posts_receipt_voucher(self):
        c = self.cheque("received", "2500", "900")
        before = balance_of(self.company, self.bank_ledger)
        cheques.clear_cheque(c, on=date(2026, 2, 3), user=self.boss)
        self.assertEqual(c.__class__.objects.get(pk=c.pk).journal_entry.voucher_type, "RV")
        self.assertEqual(balance_of(self.company, self.bank_ledger), before + Decimal("2500"))

    def test_only_pending_cheques_move_and_bounce_posts_nothing(self):
        c = self.cheque()
        cheques.bounce_cheque(c, user=self.boss)
        with self.assertRaises(ERPError):
            cheques.clear_cheque(c, user=self.boss)
        self.assertEqual(JournalEntry.objects.filter(source_type="cheque").count(), 0)
        c2 = self.cheque(number="102")
        cheques.clear_cheque(c2, user=self.boss)
        with self.assertRaises(ERPError):
            cheques.cancel_cheque(c2)

    def test_clearing_needs_post_permission(self):
        from django.core.exceptions import PermissionDenied
        clerk = get_user_model().objects.create_user("clerk", "c@example.com", "pw-12345-x")
        with self.assertRaises(PermissionDenied):
            cheques.clear_cheque(self.cheque(), user=clerk)

    def test_duplicate_cheque_number_on_same_bank_rejected(self):
        from django.db import IntegrityError, transaction
        self.cheque()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.cheque()


class FiscalYearTests(AdditionsBase):
    def year(self, y):
        return FiscalYear.objects.create(company=self.company, name=f"FY {y}", start_date=date(y, 1, 1), end_date=date(y, 12, 31))

    def test_close_locks_books_and_reopen_unlocks(self):
        fy = self.year(2026)
        periods.close_fiscal_year(fy, user=self.boss)
        self.assertEqual(AccountingPeriodLock.objects.get(company=self.company).locked_through, date(2026, 12, 31))
        with self.assertRaises(ERPError):
            create_journal_entry(self.company, date=date(2026, 6, 1), lines=[{"account": "cash", "debit": 1}, {"account": "sales_retail", "credit": 1}])
        periods.reopen_fiscal_year(fy, user=self.boss)
        self.assertFalse(AccountingPeriodLock.objects.filter(company=self.company).exists())
        create_journal_entry(self.company, date=date(2026, 6, 1), lines=[{"account": "cash", "debit": 1}, {"account": "sales_retail", "credit": 1}])

    def test_years_close_in_order_and_reopen_latest_first(self):
        y25, y26 = self.year(2025), self.year(2026)
        with self.assertRaises(ERPError):
            periods.close_fiscal_year(y26, user=self.boss)
        periods.close_fiscal_year(y25, user=self.boss)
        periods.close_fiscal_year(y26, user=self.boss)
        with self.assertRaises(ERPError):
            periods.reopen_fiscal_year(y25, user=self.boss)
        periods.reopen_fiscal_year(y26, user=self.boss)
        self.assertEqual(AccountingPeriodLock.objects.get(company=self.company).locked_through, date(2025, 12, 31))

    def test_draft_entries_block_closing_and_overlaps_are_rejected(self):
        from django.core.exceptions import ValidationError
        fy = self.year(2026)
        JournalEntry.objects.create(company=self.company, date=date(2026, 3, 1), status="draft")
        with self.assertRaises(ERPError):
            periods.close_fiscal_year(fy, user=self.boss)
        overlap = FiscalYear(company=self.company, name="FY X", start_date=date(2026, 6, 1), end_date=date(2027, 5, 31))
        with self.assertRaises(ValidationError):
            overlap.full_clean()


class RecurringJournalTests(AdditionsBase):
    def template(self, **kw):
        t = RecurringJournal.objects.create(company=self.company, name="Office rent", frequency="monthly", next_run_date=date(2026, 1, 31),
                                            narration="Monthly rent", **kw)
        RecurringJournalLine.objects.create(recurring=t, account=Account.objects.get(company=self.company, system_key="cash"), credit=Decimal("1000"))
        RecurringJournalLine.objects.create(recurring=t, account=Account.objects.filter(company=self.company, account_type="expense", is_group=False).first(), debit=Decimal("1000"))
        return t

    def test_catches_up_every_due_month_and_keeps_month_end(self):
        t = self.template()
        made = periods.generate_recurring(t, as_of=date(2026, 4, 15), user=self.boss)
        self.assertEqual([e.date for e in made], [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)])
        t.refresh_from_db()
        self.assertEqual(t.next_run_date, date(2026, 4, 30))
        self.assertTrue(all(e.status == "posted" for e in made))

    def test_running_again_does_not_double_post(self):
        t = self.template()
        periods.generate_recurring(t, as_of=date(2026, 2, 10), user=self.boss)
        again = periods.generate_recurring(t, as_of=date(2026, 2, 10), user=self.boss)
        self.assertEqual(again, [])
        self.assertEqual(JournalEntry.objects.filter(source_type="recurring_journal").count(), 1)

    def test_stops_after_end_date(self):
        t = self.template(end_date=date(2026, 2, 28))
        periods.generate_recurring(t, as_of=date(2026, 12, 31), user=self.boss)
        t.refresh_from_db()
        self.assertFalse(t.is_active)
        self.assertEqual(JournalEntry.objects.filter(source_type="recurring_journal").count(), 2)

    def test_command_posts_due_entries(self):
        self.template()
        call_command("run_recurring_journals", as_of="2026-02-01", verbosity=0)
        self.assertEqual(JournalEntry.objects.filter(source_type="recurring_journal").count(), 1)


class AccountingScreensTests(AdditionsBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.boss)

    def test_screens_render(self):
        for model in ("fiscalyear", "cheque", "recurringjournal"):
            self.assertEqual(self.client.get(reverse("erp_ui:list", args=["accounting", model])).status_code, 200, model)
            self.assertEqual(self.client.get(reverse("erp_ui:add", args=["accounting", model])).status_code, 200, model)

    def test_register_cheque_and_clear_it_from_the_list_action(self):
        resp = self.client.post(reverse("erp_ui:add", args=["accounting", "cheque"]), {
            "company": self.company.pk, "cheque_type": "issued", "bank_account": self.bank.pk, "cheque_number": "555", "party_name": "Vendor",
            "cheque_date": "2026-03-01", "amount": "750.00", "counter_account": self.ap.pk})
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["form"].errors)
        cheque = Cheque.objects.get(cheque_number="555")
        self.assertEqual((cheque.status, cheque.created_by), ("pending", self.boss))

    def test_new_accounting_reports_run(self):
        Cheque.objects.create(company=self.company, bank_account=self.bank, cheque_number="1", party_name="V", cheque_date=date(2026, 2, 1),
                              amount=Decimal("10"), counter_account=self.ap)
        FiscalYear.objects.create(company=self.company, name="FY 2026", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        for key in ("day_book", "general_ledger_all", "chart_of_accounts", "bank_balances", "fixed_asset_register", "budget_vs_actual",
                    "cheque_register", "recurring_journals", "fiscal_years"):
            resp = self.client.get(reverse("reports_erp:report", args=[key]), {"from": "2026-01-01", "to": "2026-12-31", "as_of": "2026-09-30"})
            self.assertEqual(resp.status_code, 200, key)

    def test_day_book_and_ledger_show_the_opening_entry(self):
        resp = self.client.get(reverse("reports_erp:report", args=["day_book"]), {"from": "2026-01-01", "to": "2026-01-31"})
        self.assertContains(resp, "100000")
        resp = self.client.get(reverse("reports_erp:report", args=["general_ledger_all"]), {"from": "2026-01-01", "to": "2026-01-31"})
        self.assertContains(resp, "Closing balance")
