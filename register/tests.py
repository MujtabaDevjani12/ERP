from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld, grant
from expenses.models import Expense, ExpenseCategory
from payments.models import Payment
from register import services
from register.models import Register, RegisterSession
from sales.models import PosCart, Sale


class RegisterServiceTests(TestCase):
    def setUp(self):
        self.w = PosWorld()

    def test_open_session_records_cash_and_blocks_a_second_open_on_the_same_counter(self):
        s = services.open_session(self.w.counter1, self.w.cashier, "250.50", "float from safe")
        self.assertEqual(s.status, "open")
        self.assertEqual(s.opening_cash, Decimal("250.50"))
        self.assertEqual(s.opening_note, "float from safe")
        with self.assertRaises(services.RegisterError):
            services.open_session(self.w.counter1, self.w.cashier2, 0)
        # a different counter can be open at the same time
        services.open_session(self.w.counter2, self.w.cashier2, 0)
        self.assertEqual(RegisterSession.objects.filter(status="open").count(), 2)

    def test_opening_cash_must_be_a_non_negative_number(self):
        for bad in ("abc", "-5", "nan", "inf"):
            with self.assertRaises(services.RegisterError, msg=bad):
                services.parse_amount(bad, "Opening cash")
        self.assertEqual(services.parse_amount("", "Opening cash"), Decimal("0.00"))

    def test_counter_assignment_limits_who_can_open_it(self):
        self.w.counter1.assigned_users.add(self.w.cashier)
        self.assertTrue(self.w.counter1.can_be_used_by(self.w.cashier))
        self.assertFalse(self.w.counter1.can_be_used_by(self.w.cashier2))
        self.assertTrue(self.w.counter1.can_be_used_by(self.w.manager))                 # superuser
        ids = set(services.allowed_registers(self.w.cashier2, self.w.store).values_list("id", flat=True))
        self.assertNotIn(self.w.counter1.id, ids)
        self.assertIn(self.w.counter2.id, ids)                                          # unassigned counters stay open to everyone
        with self.assertRaises(services.RegisterError):
            services.open_session(self.w.counter1, self.w.cashier2, 0)

    def test_inactive_counter_cannot_be_opened(self):
        self.w.counter1.is_active = False
        self.w.counter1.save()
        with self.assertRaises(services.RegisterError):
            services.open_session(self.w.counter1, self.w.cashier, 0)

    def test_only_the_owner_or_a_manager_can_resume_an_open_session(self):
        s = services.open_session(self.w.counter1, self.w.cashier, 0)
        self.assertTrue(services.can_resume(s, self.w.cashier))
        self.assertTrue(services.can_resume(s, self.w.manager))
        self.assertFalse(services.can_resume(s, self.w.cashier2))

    def _sell_cash(self, session, amount, method="cash"):
        sale = Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, register_session=session, reference=f"T-{Sale.objects.count() + 1}",
                                   biller=self.w.cashier, total_amount=Decimal(amount), subtotal=Decimal(amount))
        Payment.objects.create(sale=sale, reference=f"P-{Payment.objects.count() + 1}", amount=Decimal(amount), method=method,
                               register_session=session, received_by=self.w.cashier)
        return sale

    def test_summary_and_close_freeze_the_figures(self):
        s = services.open_session(self.w.counter1, self.w.cashier, "100", "")
        self._sell_cash(s, "300")
        self._sell_cash(s, "200", "card")
        self._sell_cash(s, "40", "easypaisa")
        cat = ExpenseCategory.objects.create(store=self.w.store, name="Tea")
        Expense.objects.create(store=self.w.store, category=cat, amount=Decimal("30"), method="cash", register_session=s, reference="E-1", expense_date=date.today())
        Expense.objects.create(store=self.w.store, category=cat, amount=Decimal("99"), method="card", register_session=s, reference="E-2", expense_date=date.today())
        summary = services.session_summary(s)
        self.assertEqual(summary["cash_sales"], Decimal("300.00"))
        self.assertEqual(summary["card_sales"], Decimal("200.00"))
        self.assertEqual(summary["other_payments"], Decimal("40.00"))
        self.assertEqual(summary["expenses"], Decimal("30.00"))                      # only the CASH expense leaves the drawer
        self.assertEqual(summary["expected_cash"], Decimal("370.00"))               # 100 + 300 - 30
        self.assertEqual(summary["total_sales"], Decimal("540.00"))
        self.assertEqual(summary["transactions"], 3)

        PosCart.objects.create(register_session=s, data={"items": []})
        closed = services.close_session(s.pk, self.w.cashier, "365", "short a bit")
        closed.refresh_from_db()
        self.assertEqual(closed.status, "closed")
        self.assertEqual(closed.closed_by, self.w.cashier)
        self.assertEqual(closed.expected_cash_at_close, Decimal("370.00"))
        self.assertEqual(closed.variance, Decimal("-5.00"))
        self.assertEqual(closed.closing_summary["difference"], "-5.00")
        self.assertEqual(closed.transactions_at_close, 3)
        self.assertFalse(PosCart.objects.filter(register_session=s).exists())         # cart is discarded with the shift
        # later data changes never rewrite the closing record
        self._sell_cash(s, "1000")
        closed.refresh_from_db()
        self.assertEqual(closed.expected_cash_at_close, Decimal("370.00"))
        with self.assertRaises(services.RegisterError):
            services.close_session(s.pk, self.w.cashier, "0")                       # already closed

    def test_a_closed_counter_can_be_opened_again_as_a_new_session(self):
        s = services.open_session(self.w.counter1, self.w.cashier, 0)
        services.close_session(s.pk, self.w.cashier, 0)
        s2 = services.open_session(self.w.counter1, self.w.cashier, 10)
        self.assertNotEqual(s.pk, s2.pk)


class RegisterViewTests(TestCase):
    def setUp(self):
        self.w = PosWorld()

    def login(self, user):
        self.client.login(username=user.username, password=PASSWORD)

    def test_open_view_opens_a_counter_and_stores_it_in_the_browser_session(self):
        self.login(self.w.cashier)
        resp = self.client.post(reverse("register:open"), {"action": "open", "register": self.w.counter1.pk, "opening_cash": "50"})
        self.assertRedirects(resp, reverse("dashboard:pos"), fetch_redirect_response=False)
        s = RegisterSession.objects.get()
        self.assertEqual(self.client.session["active_register_session_id"], s.pk)
        self.assertEqual(s.opening_cash, Decimal("50.00"))

    def test_cashier_cannot_open_a_counter_assigned_to_someone_else(self):
        self.w.counter1.assigned_users.add(self.w.cashier2)
        self.login(self.w.cashier)
        resp = self.client.post(reverse("register:open"), {"action": "open", "register": self.w.counter1.pk, "opening_cash": "0"})
        self.assertEqual(resp.status_code, 200)                                      # re-rendered with an error
        self.assertFalse(RegisterSession.objects.exists())

    def test_resume_after_logout_and_pos_is_gated_until_a_register_is_open(self):
        s = services.open_session(self.w.counter1, self.w.cashier, 0)
        self.login(self.w.cashier)                                                   # a fresh browser session: no active session yet
        self.assertRedirects(self.client.get(reverse("dashboard:pos")), reverse("register:open"), fetch_redirect_response=False)
        resp = self.client.post(reverse("register:open"), {"action": "resume", "register": self.w.counter1.pk})
        self.assertRedirects(resp, reverse("dashboard:pos"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["active_register_session_id"], s.pk)
        self.assertEqual(self.client.get(reverse("dashboard:pos")).status_code, 200)

    def test_pos_page_has_a_visible_close_register_button(self):
        services.open_session(self.w.counter1, self.w.cashier, 0)
        self.login(self.w.cashier)
        self.client.post(reverse("register:open"), {"action": "resume", "register": self.w.counter1.pk})
        html = self.client.get(reverse("dashboard:pos")).content.decode()
        self.assertIn("Close Register", html)
        self.assertIn("openClose()", html)

    def test_summary_api_and_close_with_json(self):
        s = services.open_session(self.w.counter1, self.w.cashier, 25)
        self.login(self.w.cashier)
        self.client.post(reverse("register:open"), {"action": "resume", "register": self.w.counter1.pk})
        data = self.client.get(reverse("register:summary")).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["summary"]["opening_cash"], "25.00")
        resp = self.client.post(reverse("register:close"), {"closing_cash_counted": "25", "note": "ok"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertTrue(resp.json()["ok"])
        s.refresh_from_db()
        self.assertEqual(s.status, "closed")
        self.assertNotIn("active_register_session_id", self.client.session)
        # closing again (second tab) is refused politely
        again = self.client.post(reverse("register:close"), {"closing_cash_counted": "25"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(again.status_code, 409)

    def test_close_rejects_a_negative_or_bad_counted_amount(self):
        services.open_session(self.w.counter1, self.w.cashier, 0)
        self.login(self.w.cashier)
        self.client.post(reverse("register:open"), {"action": "resume", "register": self.w.counter1.pk})
        resp = self.client.post(reverse("register:close"), {"closing_cash_counted": "-3"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(RegisterSession.objects.get().status, "open")


class CounterManagementTests(TestCase):
    def setUp(self):
        self.w = PosWorld()

    def test_cashier_cannot_manage_counters(self):
        self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("register:counter_list")).status_code, 403)
        resp = self.client.post(reverse("register:counter_add"), {"name": "X"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 403)

    def test_manager_creates_edits_and_deactivates_counters(self):
        self.client.login(username="manager", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("register:counter_list")).status_code, 200)
        ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        payload = {"name": "Takeaway", "code": "tk", "warehouse": self.w.warehouse.pk, "default_payment_method": "cash",
                   "assigned_users": [self.w.cashier.pk], "is_active": "on"}
        resp = self.client.post(reverse("register:counter_add"), payload, **ajax)
        self.assertTrue(resp.json()["ok"])
        counter = Register.objects.get(name="Takeaway")
        self.assertEqual(counter.code, "TK")                                          # codes are upper-cased
        self.assertEqual(list(counter.assigned_users.all()), [self.w.cashier])
        # duplicate name / code in the same branch are refused
        dup = self.client.post(reverse("register:counter_add"), {**payload, "name": "takeaway", "code": "ZZ"}, **ajax)
        self.assertEqual(dup.status_code, 200)
        self.assertIn("already has this name", dup.content.decode())
        dup = self.client.post(reverse("register:counter_add"), {**payload, "name": "Other", "code": "tk"}, **ajax)
        self.assertIn("already uses this code", dup.content.decode())
        # edit
        resp = self.client.post(reverse("register:counter_edit", args=[counter.pk]), {**payload, "name": "Takeaway 2"}, **ajax)
        self.assertTrue(resp.json()["ok"])
        counter.refresh_from_db()
        self.assertEqual(counter.name, "Takeaway 2")
        # toggle off, then on
        self.client.post(reverse("register:counter_toggle", args=[counter.pk]))
        counter.refresh_from_db()
        self.assertFalse(counter.is_active)
        self.client.post(reverse("register:counter_toggle", args=[counter.pk]))
        counter.refresh_from_db()
        self.assertTrue(counter.is_active)

    def test_a_counter_with_an_open_session_cannot_be_switched_off(self):
        services.open_session(self.w.counter1, self.w.cashier, 0)
        self.client.login(username="manager", password=PASSWORD)
        self.client.post(reverse("register:counter_toggle", args=[self.w.counter1.pk]))
        self.w.counter1.refresh_from_db()
        self.assertTrue(self.w.counter1.is_active)
