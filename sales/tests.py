import json
from decimal import Decimal

from django.apps import apps
from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld
from payments.models import Payment, PaymentMethod
from register import services
from sales.models import PosCart, Sale

OUT_OF_STOCK = "Product is currently out of stock."
STOCK_CHANGED = "Stock changed. Please review your cart."


class PosBase(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="cashier", password=PASSWORD)
        self.session = services.open_session(self.w.counter1, self.w.cashier, 100)
        self.activate(self.client, self.session)

    def activate(self, client, session):
        s = client.session
        s["active_register_session_id"] = session.pk
        s.save()

    def post(self, name, payload, *args, client=None):
        return (client or self.client).post(reverse(name, args=args), data=json.dumps(payload), content_type="application/json")

    def line(self, variant, qty=1, **extra):
        return {"variant_id": variant.pk, "quantity": str(qty), **extra}

    def checkout(self, items, payments=None, **extra):
        body = {"items": items, "payments": payments if payments is not None else [], **extra}
        return self.post("sales:pos_finalize", body)


class OutOfStockTests(PosBase):
    def lookup(self, **params):
        return self.client.get(reverse("catalog:api_variant_lookup"), params).json()["results"]

    def test_pos_lookup_hides_products_with_no_stock_everywhere(self):
        names = {r["name"] for r in self.lookup(pos="1")}
        self.assertIn("Tea", names)
        self.assertNotIn("Empty shelf", names)
        # search, barcode scan and category filtering use the same rule
        self.assertEqual(self.lookup(pos="1", q="BCEMPTY"), [])
        self.assertEqual(self.lookup(pos="1", q="Empty"), [])
        self.assertEqual([r["name"] for r in self.lookup(pos="1", q="BCTEA")], ["Tea"])

    def test_lookup_reports_how_many_variants_a_product_has_on_sale(self):
        from catalog.models import ProductVariant
        from inventory.models import StockItem
        small = ProductVariant.objects.create(product=self.w.tea.product, name="Small", sku="TEA-S", selling_price=Decimal("90"), cost_price=Decimal("10"))
        gone = ProductVariant.objects.create(product=self.w.tea.product, name="Big", sku="TEA-B", selling_price=Decimal("110"), cost_price=Decimal("10"))
        StockItem.objects.create(variant=small, warehouse=self.w.warehouse, quantity=Decimal("4"))
        StockItem.objects.create(variant=gone, warehouse=self.w.warehouse, quantity=Decimal("0"))
        rows = {r["id"]: r for r in self.lookup(pos="1")}
        # the tile list only offers what can be sold, but every active variant counts: the POS asks which one to add
        self.assertEqual(rows[self.w.tea.pk]["variant_count"], 3)
        self.assertEqual(rows[small.pk]["variant_count"], 3)
        self.assertNotIn(gone.pk, rows)
        self.assertEqual(rows[self.w.milk.pk]["variant_count"], 1)
        # the variant picker: every variant of the product, the empty one flagged so the screen can grey it out
        picked = {r["id"]: r for r in self.lookup(pos="1", product=str(self.w.tea.product_id), all="1")}
        self.assertEqual(set(picked), {self.w.tea.pk, small.pk, gone.pk})
        self.assertTrue(picked[gone.pk]["out_of_stock"])
        self.assertFalse(picked[small.pk]["out_of_stock"])
        # without all=1 the picker-style call still hides the empty variant (quick view)
        self.assertEqual({r["id"] for r in self.lookup(pos="1", product=str(self.w.tea.product_id))}, {self.w.tea.pk, small.pk})

    def test_pos_screen_passes_the_customer_rule_to_the_page(self):
        from configuration.models import POSConfiguration
        resp = self.client.get(reverse("dashboard:pos"))
        self.assertContains(resp, "requireCustomer: true")
        POSConfiguration.objects.update_or_create(store=self.w.store, defaults={"require_customer": False})
        resp = self.client.get(reverse("dashboard:pos"))
        self.assertContains(resp, "requireCustomer: false")
        self.assertContains(resp, "variant picker")

    def test_negative_stock_is_hidden_and_zero_stock_stays_visible_in_the_erp_lookup(self):
        from inventory.models import StockItem
        StockItem.objects.filter(variant=self.w.milk).update(quantity=Decimal("-2"))
        self.assertNotIn("Milk", {r["name"] for r in self.lookup(pos="1")})
        # ERP / scanner lookups (no pos=1) still show it
        self.assertIn("Empty shelf", {r["name"] for r in self.lookup()})
        self.assertIn("Milk", {r["name"] for r in self.lookup()})

    def test_product_reappears_once_it_is_back_in_stock(self):
        from inventory.models import StockItem
        StockItem.objects.filter(variant=self.w.empty).update(quantity=Decimal("3"))
        self.assertIn("Empty shelf", {r["name"] for r in self.lookup(pos="1")})

    def test_backend_rejects_an_out_of_stock_item_even_if_the_screen_sends_it(self):
        resp = self.checkout([self.line(self.w.empty)], [{"method": "cash", "amount": "30"}])
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], OUT_OF_STOCK)
        self.assertEqual(body["code"], "out_of_stock")
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(Payment.objects.count(), 0)

    def test_stock_changed_when_another_sale_took_the_stock(self):
        resp = self.checkout([self.line(self.w.milk, 6)], [{"method": "cash", "amount": "300"}])    # only 5 on the shelf
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], STOCK_CHANGED)
        self.assertEqual(self.w.stock_of(self.w.milk), Decimal("5"))
        self.assertEqual(Sale.objects.count(), 0)

    def test_stock_check_endpoint_reports_problems(self):
        resp = self.post("sales:pos_stock_check", {"items": [self.line(self.w.empty), self.line(self.w.milk, 9), self.line(self.w.tea, 2)]}).json()
        self.assertFalse(resp["ok"])
        messages = {p["variant_id"]: p["message"] for p in resp["problems"]}
        self.assertEqual(messages[self.w.empty.pk], OUT_OF_STOCK)
        self.assertEqual(messages[self.w.milk.pk], STOCK_CHANGED)
        self.assertNotIn(self.w.tea.pk, messages)


class CheckoutTests(PosBase):
    def test_cash_sale_takes_stock_records_payment_and_returns_change(self):
        resp = self.checkout([self.line(self.w.tea, 2)], [{"method": "cash", "amount": "300"}])       # 2 x 100 + 10% tax = 220
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["total"], "220.00")
        self.assertEqual(data["paid"], "220.00")
        self.assertEqual(data["change"], "80.00")
        self.assertEqual(self.w.stock_of(self.w.tea), Decimal("8"))
        sale = Sale.objects.get()
        self.assertEqual(sale.status, "completed")
        self.assertEqual(sale.register_session, self.session)
        self.assertEqual(sale.payment_status, "paid")
        pay = Payment.objects.get()
        self.assertEqual((pay.method, pay.amount, pay.status), ("cash", Decimal("220.00"), "completed"))
        self.assertEqual(pay.register_session, self.session)
        self.assertEqual(pay.received_by, self.w.cashier)
        self.assertTrue(pay.reference)

    def test_split_payment_with_references_is_stored_line_by_line(self):
        resp = self.checkout([self.line(self.w.milk, 2)], [
            {"method": "card", "amount": "40", "reference_no": "AUTH-123"},
            {"method": "easypaisa", "amount": "20", "reference_no": "EP-9"},
            {"method": "cash", "amount": "50"},                                                     # 100 total: 40 + 20 + 40 cash (10 change)
        ])
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["change"], "10.00")
        rows = {p.method: p for p in Payment.objects.all()}
        self.assertEqual(set(rows), {"card", "easypaisa", "cash"})
        self.assertEqual(rows["card"].reference_no, "AUTH-123")
        self.assertEqual(rows["easypaisa"].reference_no, "EP-9")
        self.assertEqual(rows["cash"].amount, Decimal("40.00"))
        self.assertEqual(sum(p.amount for p in rows.values()), Decimal("100.00"))

    def test_non_cash_methods_need_a_reference(self):
        resp = self.checkout([self.line(self.w.milk)], [{"method": "card", "amount": "50"}])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "reference_required")
        self.assertEqual(Sale.objects.count(), 0)
        self.assertEqual(self.w.stock_of(self.w.milk), Decimal("5"))

    def test_card_cannot_be_over_tendered_and_unknown_or_disabled_methods_are_refused(self):
        resp = self.checkout([self.line(self.w.milk)], [{"method": "card", "amount": "80", "reference_no": "x"}])
        self.assertEqual(resp.json()["code"], "overpay")
        PaymentMethod.objects.filter(store=self.w.store, code="jazzcash").update(is_active=False)
        resp = self.checkout([self.line(self.w.milk)], [{"method": "jazzcash", "amount": "50", "reference_no": "x"}])
        self.assertEqual(resp.json()["code"], "bad_payment")
        resp = self.checkout([self.line(self.w.milk)], [{"method": "bitcoin", "amount": "50"}])
        self.assertEqual(resp.json()["code"], "bad_payment")
        self.assertEqual(Sale.objects.count(), 0)

    def test_short_payment_needs_a_customer_for_credit(self):
        resp = self.checkout([self.line(self.w.milk)], [{"method": "cash", "amount": "20"}])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "underpaid")
        from customers.models import Customer
        customer = Customer.objects.create(store=self.w.store, name="Ali")
        resp = self.checkout([self.line(self.w.milk)], [{"method": "cash", "amount": "20"}], customer_id=customer.pk)
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["due_amount"], "30.00")
        self.assertEqual(Sale.objects.get().payment_status, "partial")

    def test_pay_later_and_half_payment_work_for_a_customer_and_the_balance_can_be_paid_afterwards(self):
        from customers.models import Customer
        customer = Customer.objects.create(store=self.w.store, name="Sara")
        resp = self.checkout([self.line(self.w.tea, 2)], [], customer_id=customer.pk)          # 220 on credit, nothing paid now
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual((resp.json()["paid"], resp.json()["due_amount"]), ("0", "220.00"))
        sale = Sale.objects.get()
        self.assertEqual((sale.status, sale.payment_status, Payment.objects.count()), ("completed", "due", 0))
        self.assertEqual(self.w.stock_of(self.w.tea), Decimal("8"))                              # stock still leaves the shelf
        resp = self.checkout([self.line(self.w.milk, 2)], [{"method": "cash", "amount": "50"}], customer_id=customer.pk)
        self.assertEqual((resp.status_code, resp.json()["due_amount"]), (200, "50.00"))          # half now (100 total)
        half = Sale.objects.get(reference=resp.json()["reference"])
        self.assertEqual(half.payment_status, "partial")
        pay = self.client.post(reverse("sales:sale_add_payment", args=[half.pk]), {"method": "cash", "amount": "50"})
        self.assertIn(pay.status_code, (200, 302))
        self.assertEqual(Sale.objects.get(pk=half.pk).payment_status, "paid")

    def test_pos_page_has_the_customer_picker_and_part_payment_buttons_in_the_payment_window(self):
        html = self.client.get(reverse("dashboard:pos")).content.decode()
        for needle in ("pay-customer", "Pay half now", "Pay later (credit)", "openAddCustomer"):
            self.assertIn(needle, html)

    def test_invalid_numbers_are_rejected_without_a_traceback(self):
        for qty in ("abc", "-1", "0", "nan"):
            resp = self.checkout([self.line(self.w.milk, qty)], [{"method": "cash", "amount": "50"}])
            self.assertEqual(resp.status_code, 400, qty)
        resp = self.checkout([self.line(self.w.milk, 1, discount_amount="999")], [{"method": "cash", "amount": "50"}])
        self.assertEqual(resp.status_code, 400)
        resp = self.checkout([{"variant_id": 999999, "quantity": "1"}], [{"method": "cash", "amount": "50"}])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.client.post(reverse("sales:pos_finalize"), data="not json", content_type="application/json").status_code, 400)
        self.assertEqual(Sale.objects.count(), 0)

    def test_same_checkout_token_never_creates_two_sales(self):
        body = {"checkout_token": "tok-1", "items": [self.line(self.w.tea)], "payments": [{"method": "cash", "amount": "110"}]}
        first = self.post("sales:pos_finalize", body).json()
        second = self.post("sales:pos_finalize", body).json()
        self.assertTrue(first["ok"] and second["ok"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["sale_id"], second["sale_id"])
        self.assertEqual(Sale.objects.count(), 1)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(self.w.stock_of(self.w.tea), Decimal("9"))                                  # taken once, not twice

    def test_checkout_is_refused_when_the_register_is_closed(self):
        services.close_session(self.session.pk, self.w.cashier, 0)
        resp = self.checkout([self.line(self.w.tea)], [{"method": "cash", "amount": "110"}])
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "no_register")
        self.assertEqual(Sale.objects.count(), 0)

    def test_order_discount_is_spread_over_the_lines_and_quote_matches_the_sale(self):
        items = [self.line(self.w.tea, 2), self.line(self.w.milk, 1)]                                  # 200 + 50 = 250, tax on tea
        discount = {"type": "percent", "value": "10"}
        quote = self.post("sales:pos_quote", {"items": items, "order_discount": discount}).json()
        self.assertTrue(quote["ok"])
        self.assertEqual(quote["subtotal"], "250.00")
        self.assertEqual(quote["discount"], "25.00")
        resp = self.checkout(items, [{"method": "cash", "amount": "1000"}], order_discount=discount)
        sale = Sale.objects.get()
        self.assertEqual(str(sale.total_amount), quote["total"])                                       # screen total == saved total
        self.assertEqual(str(sale.tax_amount), quote["tax"])
        self.assertEqual(sale.discount_amount, Decimal("25.00"))
        self.assertEqual(resp.json()["total"], quote["total"])

    def test_checkout_clears_the_server_cart(self):
        PosCart.objects.create(register_session=self.session, data={"items": [{"variant_id": self.w.tea.pk, "quantity": "1"}]})
        self.checkout([self.line(self.w.tea)], [{"method": "cash", "amount": "110"}])
        self.assertFalse(PosCart.objects.filter(register_session=self.session).exists())

    def test_sale_counts_in_the_register_summary(self):
        self.checkout([self.line(self.w.tea)], [{"method": "cash", "amount": "110"}])
        self.checkout([self.line(self.w.milk)], [{"method": "card", "amount": "50", "reference_no": "r"}])
        summary = services.session_summary(self.session)
        self.assertEqual(summary["cash_sales"], Decimal("110.00"))
        self.assertEqual(summary["card_sales"], Decimal("50.00"))
        self.assertEqual(summary["transactions"], 2)
        self.assertEqual(summary["expected_cash"], Decimal("210.00"))                                 # 100 float + 110 cash


class HoldResumeTests(PosBase):
    def test_hold_parks_the_sale_without_touching_stock_or_payments(self):
        resp = self.checkout([self.line(self.w.tea, 3)], hold=True)
        self.assertEqual(resp.status_code, 200, resp.content)
        sale = Sale.objects.get()
        self.assertEqual(sale.status, "held")
        self.assertEqual(self.w.stock_of(self.w.tea), Decimal("10"))
        self.assertEqual(Payment.objects.count(), 0)
        held = self.client.get(reverse("sales:held_sales_api")).json()["sales"]
        self.assertEqual([h["reference"] for h in held], [sale.reference])

    def test_resume_moves_the_held_sale_into_the_server_cart(self):
        self.checkout([self.line(self.w.tea, 3)], hold=True)
        sale = Sale.objects.get()
        data = self.post("sales:resume_held_sale", {}, sale.pk).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["cart"]["items"][0]["quantity"], "3.000")
        self.assertFalse(Sale.objects.exists())
        self.assertEqual(PosCart.objects.get(register_session=self.session).data["items"][0]["variant_id"], self.w.tea.pk)

    def test_resume_is_refused_while_the_cart_has_items(self):
        self.checkout([self.line(self.w.tea)], hold=True)   # holding empties the server cart...
        PosCart.objects.update_or_create(register_session=self.session, defaults={
            "data": {"items": [{"variant_id": self.w.milk.pk, "quantity": "1"}]}})   # ...then the cashier starts a new sale
        resp = self.post("sales:resume_held_sale", {}, Sale.objects.get().pk)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(Sale.objects.get().status, "held")

    def test_several_held_sales_can_exist_and_resume_drops_items_that_ran_out(self):
        self.checkout([self.line(self.w.tea)], hold=True)
        self.checkout([self.line(self.w.milk)], hold=True)
        self.assertEqual(Sale.objects.filter(status="held").count(), 2)
        from inventory.models import StockItem
        StockItem.objects.filter(variant=self.w.milk).update(quantity=0)
        milk_sale = Sale.objects.get(items__variant=self.w.milk)
        data = self.post("sales:resume_held_sale", {}, milk_sale.pk).json()
        self.assertEqual(data["cart"]["items"], [])
        self.assertTrue(any("out of stock" in n.lower() for n in data["notices"]))


class CartPersistenceTests(PosBase):
    def cart(self, **over):
        base = {"items": [{"variant_id": self.w.tea.pk, "name": "Tea", "unit_price": "100", "quantity": "2", "stock": "10"}],
                "customer": None, "order_discount": {"type": "amount", "value": "0"}, "sale_note": "", "staff_note": ""}
        base.update(over)
        return base

    def test_cart_is_saved_on_the_server_and_comes_back_after_a_refresh(self):
        self.assertTrue(self.post("sales:pos_cart_save", {"cart": self.cart(sale_note="gift wrap")}).json()["ok"])
        data = self.client.get(reverse("sales:pos_cart_get")).json()
        self.assertEqual(data["cart"]["items"][0]["variant_id"], self.w.tea.pk)
        self.assertEqual(data["cart"]["items"][0]["quantity"], "2.000")
        self.assertEqual(data["cart"]["sale_note"], "gift wrap")

    def test_payment_data_is_never_stored_with_the_cart(self):
        cart = self.cart()
        cart["payments"] = [{"method": "card", "amount": "99", "card_number": "4111111111111111"}]
        cart["card_number"] = "4111111111111111"
        self.post("sales:pos_cart_save", {"cart": cart})
        stored = json.dumps(PosCart.objects.get().data)
        self.assertNotIn("4111", stored)
        self.assertNotIn("payments", stored)

    def test_restored_cart_drops_out_of_stock_lines_and_trims_quantities(self):
        cart = self.cart(items=[
            {"variant_id": self.w.tea.pk, "name": "Tea", "unit_price": "100", "quantity": "50", "stock": "50"},
            {"variant_id": self.w.empty.pk, "name": "Empty shelf", "unit_price": "30", "quantity": "1", "stock": "1"},
        ])
        self.post("sales:pos_cart_save", {"cart": cart})
        data = self.client.get(reverse("sales:pos_cart_get")).json()
        self.assertEqual([i["variant_id"] for i in data["cart"]["items"]], [self.w.tea.pk])
        self.assertEqual(Decimal(data["cart"]["items"][0]["quantity"]), Decimal("10"))
        self.assertEqual(len(data["notices"]), 2)
        self.assertTrue(any(OUT_OF_STOCK in n for n in data["notices"]))

    def test_each_counter_has_its_own_cart(self):
        self.post("sales:pos_cart_save", {"cart": self.cart()})
        other_session = services.open_session(self.w.counter2, self.w.cashier2, 0)
        other = self.client_class()
        other.login(username="cashier2", password=PASSWORD)
        self.activate(other, other_session)
        self.assertIsNone(other.get(reverse("sales:pos_cart_get")).json()["cart"])
        self.post("sales:pos_cart_save", {"cart": self.cart(items=[{"variant_id": self.w.milk.pk, "name": "Milk", "unit_price": "50", "quantity": "1"}])}, client=other)
        mine = self.client.get(reverse("sales:pos_cart_get")).json()["cart"]
        self.assertEqual(mine["items"][0]["variant_id"], self.w.tea.pk)
        self.assertEqual(PosCart.objects.count(), 2)

    def test_clear_and_empty_save_remove_the_cart_and_closing_discards_it(self):
        self.post("sales:pos_cart_save", {"cart": self.cart()})
        self.post("sales:pos_cart_clear", {})
        self.assertFalse(PosCart.objects.exists())
        self.post("sales:pos_cart_save", {"cart": self.cart()})
        self.post("sales:pos_cart_save", {"cart": {"items": []}})
        self.assertFalse(PosCart.objects.exists())
        self.post("sales:pos_cart_save", {"cart": self.cart()})
        services.close_session(self.session.pk, self.w.cashier, 0)
        self.assertFalse(PosCart.objects.exists())

    def test_cart_api_needs_an_open_register(self):
        services.close_session(self.session.pk, self.w.cashier, 0)
        self.assertEqual(self.client.get(reverse("sales:pos_cart_get")).status_code, 409)
        self.assertEqual(self.post("sales:pos_cart_save", {"cart": self.cart()}).status_code, 409)


class PaymentMethodTests(TestCase):
    def setUp(self):
        self.w = PosWorld()

    def test_defaults_include_the_pakistani_wallets_and_are_not_duplicated(self):
        PaymentMethod.ensure_defaults(self.w.store)
        PaymentMethod.ensure_defaults(self.w.store)
        codes = list(PaymentMethod.objects.filter(store=self.w.store).values_list("code", flat=True))
        for code in ("cash", "card", "bank_transfer", "easypaisa", "jazzcash", "other"):
            self.assertEqual(codes.count(code), 1, code)

    def test_manager_can_rename_reorder_disable_and_add_methods(self):
        self.client.login(username="manager", password=PASSWORD)
        ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        card = PaymentMethod.objects.get(store=self.w.store, code="card")
        resp = self.client.post(reverse("payments:method_edit", args=[card.pk]),
                                {"name": "Debit / credit card", "code": "ignored", "sort_order": "5", "requires_reference": "on", "is_active": "on"}, **ajax)
        self.assertTrue(resp.json()["ok"])
        card.refresh_from_db()
        self.assertEqual((card.name, card.sort_order, card.code), ("Debit / credit card", 5, "card"))      # the code never changes
        resp = self.client.post(reverse("payments:method_add"), {"name": "NayaPay", "code": "nayapay", "sort_order": "90", "is_active": "on"}, **ajax)
        self.assertTrue(resp.json()["ok"])
        dup = self.client.post(reverse("payments:method_add"), {"name": "Another", "code": "nayapay", "sort_order": "1", "is_active": "on"}, **ajax)
        self.assertIn("already has a payment method", dup.content.decode())
        self.client.post(reverse("payments:method_toggle", args=[card.pk]))
        card.refresh_from_db()
        self.assertFalse(card.is_active)
        self.assertNotIn("card", [m.code for m in PaymentMethod.active_for(self.w.store)])

    def test_at_least_one_method_must_stay_enabled(self):
        self.client.login(username="manager", password=PASSWORD)
        PaymentMethod.objects.filter(store=self.w.store).exclude(code="cash").update(is_active=False)
        cash = PaymentMethod.objects.get(store=self.w.store, code="cash")
        self.client.post(reverse("payments:method_toggle", args=[cash.pk]))
        cash.refresh_from_db()
        self.assertTrue(cash.is_active)

    def test_cashier_cannot_open_payment_method_settings(self):
        self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("payments:method_list")).status_code, 403)


class ReturnTests(PosBase):
    def sell(self, variant, qty, amount):
        data = self.checkout([self.line(variant, qty)], [{"method": "cash", "amount": amount}]).json()
        return Sale.objects.get(pk=data["sale_id"])

    def test_return_puts_stock_back_records_the_refund_and_updates_the_sale(self):
        from payments.models import Refund
        sale = self.sell(self.w.milk, 2, "100")                                                      # 100 paid in cash
        self.assertEqual(self.w.stock_of(self.w.milk), Decimal("3"))
        item = sale.items.get()
        resp = self.client.post(reverse("sales:sale_return", args=[sale.pk]),
                                {f"qty_{item.pk}": "1", "reason": "damaged", "refund_method": "cash", "restock": "on"})
        self.assertEqual(resp.status_code, 302, resp.content[:500])
        self.assertEqual(self.w.stock_of(self.w.milk), Decimal("4"))
        refund = Refund.objects.get()
        self.assertEqual((refund.amount, refund.method), (Decimal("50.00"), "cash"))
        sale.refresh_from_db()
        self.assertEqual(sale.status, "partially_returned")
        # the cash refund leaves the till
        self.assertEqual(services.session_summary(self.session)["expected_cash"], Decimal("150.00"))   # 100 float + 100 sale - 50 refund
        self.client.post(reverse("sales:sale_return", args=[sale.pk]), {f"qty_{item.pk}": "1", "reason": "again", "refund_method": "cash", "restock": "on"})
        sale.refresh_from_db()
        self.assertEqual(sale.status, "returned")

    def test_cannot_return_more_than_was_sold_or_without_a_reason(self):
        sale = self.sell(self.w.milk, 1, "50")
        item = sale.items.get()
        url = reverse("sales:sale_return", args=[sale.pk])
        self.client.post(url, {f"qty_{item.pk}": "2", "reason": "x", "refund_method": "cash"})
        self.client.post(url, {f"qty_{item.pk}": "1", "reason": "", "refund_method": "cash"})
        sale.refresh_from_db()
        self.assertEqual(sale.status, "completed")
        self.assertEqual(self.w.stock_of(self.w.milk), Decimal("4"))

    def test_pos_return_button_finds_the_sale_by_invoice_number(self):
        sale = self.sell(self.w.milk, 1, "50")
        data = self.client.get(reverse("sales:pos_find_sale"), {"ref": sale.reference}).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["return_url"], reverse("sales:sale_return", args=[sale.pk]))
        self.assertFalse(self.client.get(reverse("sales:pos_find_sale"), {"ref": "NOPE-1"}).json()["ok"])


class PosWindowReturnTests(ReturnTests):
    """The POS shows orders / returns / exchanges in its own windows: these JSON endpoints feed them (no page change)."""

    def test_orders_window_lists_and_searches_sales(self):
        sale = self.sell(self.w.milk, 1, "50")
        data = self.client.get(reverse("sales:pos_orders")).json()
        self.assertTrue(data["ok"])
        self.assertIn(sale.reference, [s["reference"] for s in data["sales"]])
        self.assertTrue(next(s for s in data["sales"] if s["id"] == sale.pk)["can_return"])
        self.assertEqual(self.client.get(reverse("sales:pos_orders"), {"q": "NOPE-XYZ"}).json()["sales"], [])
        self.assertEqual(len(self.client.get(reverse("sales:pos_orders"), {"q": sale.reference}).json()["sales"]), 1)

    def test_find_sale_gives_the_id_the_window_opens(self):
        sale = self.sell(self.w.milk, 1, "50")
        self.assertEqual(self.client.get(reverse("sales:pos_find_sale"), {"ref": sale.reference}).json()["sale_id"], sale.pk)

    def test_sale_info_has_lines_methods_and_refundable_balance(self):
        sale = self.sell(self.w.milk, 2, "100")
        d = self.client.get(reverse("sales:pos_sale_info", args=[sale.pk])).json()
        self.assertTrue(d["ok"])
        info = d["sale"]
        self.assertEqual((info["reference"], info["can_return"], info["balance"]), (sale.reference, True, "100.00"))
        self.assertEqual(len(info["lines"]), 1)
        self.assertEqual((info["lines"][0]["left"], info["lines"][0]["unit_refund"]), ("2", "50.00"))
        self.assertIn("cash", [m["code"] for m in info["methods"]])
        self.assertEqual(self.client.get(reverse("sales:pos_sale_info", args=[999999])).status_code, 404)

    def test_return_from_the_pos_window_restocks_refunds_and_stays_on_json(self):
        import json
        from payments.models import Refund
        sale = self.sell(self.w.milk, 2, "100")
        item = sale.items.get()
        resp = self.client.post(reverse("sales:pos_do_return"), json.dumps({"sale_id": sale.pk, "qty": {str(item.pk): "1"}, "reason": "damaged", "method": "cash", "restock": True}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content[:400])
        d = resp.json()
        self.assertTrue(d["ok"], d)
        self.assertEqual((d["sale"], d["refund"], d["exchange"]), (sale.reference, "50.00", False))
        self.assertEqual(self.w.stock_of(self.w.milk), Decimal("4"))
        self.assertEqual(Refund.objects.get().amount, Decimal("50.00"))
        sale.refresh_from_db()
        self.assertEqual(sale.status, "partially_returned")

    def test_exchange_from_the_pos_window_defaults_the_reason(self):
        import json
        sale = self.sell(self.w.milk, 1, "50")
        item = sale.items.get()
        d = self.client.post(reverse("sales:pos_do_return"), json.dumps({"sale_id": sale.pk, "qty": {str(item.pk): "1"}, "method": "cash", "exchange": True}),
                             content_type="application/json").json()
        self.assertTrue(d["ok"], d)
        self.assertTrue(d["exchange"])

    def test_pos_return_rejects_bad_requests_with_a_message(self):
        import json
        sale = self.sell(self.w.milk, 1, "50")
        item = sale.items.get()
        url = reverse("sales:pos_do_return")
        def post(body): return self.client.post(url, json.dumps(body), content_type="application/json")
        r = post({"sale_id": sale.pk, "qty": {str(item.pk): "5"}, "reason": "x", "method": "cash"})
        self.assertEqual(r.status_code, 400); self.assertFalse(r.json()["ok"])
        self.assertEqual(post({"sale_id": sale.pk, "qty": {}, "reason": "x", "method": "cash"}).status_code, 400)
        self.assertEqual(post({"sale_id": sale.pk, "qty": {str(item.pk): "1"}, "reason": "", "method": "cash"}).status_code, 400)
        self.assertEqual(post({"sale_id": 999999, "qty": {}, "reason": "x"}).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 405)
        sale.refresh_from_db()
        self.assertEqual(sale.status, "completed")
