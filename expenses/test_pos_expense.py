"""Expense paid from the till at the POS: ties to the register session, lowers expected cash, validated, permission-guarded."""
import json
from decimal import Decimal

from django.urls import reverse

from core.pos_fixtures import PASSWORD, grant
from expenses.models import Expense, ExpenseCategory
from register import services
from sales.tests import PosBase


class PosExpenseTests(PosBase):
    def setUp(self):
        super().setUp()                                                  # cashier, counter 1 open with 100 float
        grant(self.w.cashier, "expenses.add_expense")
        self.cat = ExpenseCategory.objects.create(store=self.w.store, name="Tea & snacks")

    def spend(self, **over):
        body = {"category_id": self.cat.pk, "amount": "30", "paid_to": "Shop", "note": "milk"}
        body.update(over)
        return self.post("expenses:pos_expense", body)

    def test_expense_is_saved_against_the_session_and_lowers_expected_cash(self):
        before = services.session_summary(self.session)["expected_cash"]
        r = self.spend()
        self.assertTrue(r.json()["ok"], r.json())
        e = Expense.objects.get()
        self.assertEqual((e.register_session, e.method, e.amount, e.created_by, e.store), (self.session, "cash", 30, self.w.cashier, self.w.store))
        self.assertTrue(e.reference)
        self.assertEqual(services.session_summary(self.session)["expected_cash"], before - 30)
        self.assertEqual(r.json()["till"], str(before - 30))

    def test_cannot_pay_out_more_than_the_till_holds(self):
        r = self.spend(amount="500")
        self.assertFalse(r.json()["ok"])
        self.assertIn("till only holds", r.json()["error"])
        self.assertEqual(Expense.objects.count(), 0)

    def test_validation(self):
        for bad in ("0", "-3", "abc", "", "nan"):
            self.assertFalse(self.spend(amount=bad).json()["ok"], bad)
        self.assertEqual(self.spend(category_id="").json()["field"], "category")
        self.assertEqual(self.spend(category_id="999999").json()["field"], "category")
        self.assertEqual(Expense.objects.count(), 0)

    def test_other_branch_category_is_refused(self):
        from tenants.models import Store
        other = ExpenseCategory.objects.create(store=Store.objects.create(name="Other", company=self.w.company), name="Rent")
        self.assertFalse(self.spend(category_id=other.pk).json()["ok"])

    def test_no_open_register(self):
        s = self.client.session; s.pop("active_register_session_id"); s.save()
        r = self.spend()
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_register"))

    def test_permission_and_bad_body(self):
        self.w.cashier.user_permissions.clear()
        grant(self.w.cashier, "sales.add_sale")
        self.assertEqual(self.spend().status_code, 403)
        grant(self.w.cashier, "expenses.add_expense")
        r = self.client.post(reverse("expenses:pos_expense"), data="not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_pos_page_shows_the_button_only_with_permission(self):
        page = self.client.get(reverse("dashboard:pos"))
        self.assertContains(page, "Pay an expense from the till"); self.assertContains(page, "Tea &amp; snacks")
        self.w.cashier.user_permissions.remove(*self.w.cashier.user_permissions.filter(codename="add_expense"))
        self.w.cashier = type(self.w.cashier).objects.get(pk=self.w.cashier.pk)
        self.assertNotContains(self.client.get(reverse("dashboard:pos")), "Pay an expense from the till")
