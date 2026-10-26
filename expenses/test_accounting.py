"""Expenses reach the Profit & Loss: Dr expense account / Cr Cash or Bank, kept in step when edited or deleted."""
import datetime
from decimal import Decimal

from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase

from accounting.models import Account, JournalEntry, JournalLine
from company.models import Company
from expenses.accounting import expense_account_key
from expenses.models import Expense, ExpenseCategory
from tenants.models import Store


class ExpenseBooksTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.cat = ExpenseCategory.objects.create(store=self.store, name="Shop Rent")

    def bal(self, key):
        acc = Account.objects.get(company=self.company, system_key=key)
        a = JournalLine.objects.filter(account=acc, entry__status__in=("posted", "reversed")).aggregate(d=Sum("debit"), c=Sum("credit"))
        return (a["d"] or 0) - (a["c"] or 0)

    def make(self, amount="100", method="cash"):
        return Expense.objects.create(reference=f"EXP-{Expense.objects.count() + 1}", store=self.store, category=self.cat, amount=Decimal(amount),
                                      method=method, expense_date=datetime.date.today())

    def test_account_picked_from_category_name(self):
        self.assertEqual(expense_account_key("Shop Rent"), "rent")
        self.assertEqual(expense_account_key("Electricity bill"), "utilities")
        self.assertEqual(expense_account_key("Tea & snacks"), "other_expense")

    def test_expense_posts_cash_out_and_expense_up(self):
        self.make("100")
        self.assertEqual(self.bal("rent"), Decimal("100"))
        self.assertEqual(self.bal("cash"), Decimal("-100"))

    def test_card_expense_credits_bank(self):
        self.make("40", method="card")
        self.assertEqual(self.bal("bank"), Decimal("-40"))

    def test_editing_replaces_the_entry(self):
        e = self.make("100")
        e.amount = Decimal("150")
        e.save()
        self.assertEqual(self.bal("rent"), Decimal("150"))
        self.assertEqual(self.bal("cash"), Decimal("-150"))
        e.save()                                                          # saving without a change adds nothing
        self.assertEqual(self.bal("rent"), Decimal("150"))

    def test_deleting_takes_it_out_of_the_books(self):
        e = self.make("100")
        e.delete()
        self.assertEqual(self.bal("rent"), 0)
        self.assertEqual(self.bal("cash"), 0)

    def test_missing_chart_does_not_block_the_expense(self):
        other = Company.objects.create(name="NoChart")
        st = Store.objects.create(name="B", company=other)
        cat = ExpenseCategory.objects.create(store=st, name="Misc")
        e = Expense.objects.create(reference="EXP-NC", store=st, category=cat, amount=Decimal("5"), expense_date=datetime.date.today())
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())
        self.assertFalse(JournalEntry.objects.filter(company=other).exists())

    def test_backfill_posts_old_expenses_once(self):
        e = self.make("70")
        JournalEntry.objects.filter(source_type="expense").update(source_type="x")       # pretend it was saved before the feature existed
        call_command("backfill_expense_gl", verbosity=0)
        self.assertFalse(JournalEntry.objects.filter(source_type="expense").exists())
        call_command("backfill_expense_gl", "--apply", verbosity=0)
        call_command("backfill_expense_gl", "--apply", verbosity=0)
        self.assertEqual(JournalEntry.objects.filter(source_type="expense").count(), 1)
