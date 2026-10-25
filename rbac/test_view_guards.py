"""Direct URLs are guarded server-side, not just hidden from the sidebar."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld, grant

LISTS = {
    "catalog:product_list": "catalog.view_product",
    "customers:customer_list": "customers.view_customer",
    "inventory:stock_list": "inventory.view_stockitem",
    "inventory:adjustment_list": "inventory.view_stockadjustment",
    "inventory:rack_list": "inventory.view_rack",
    "suppliers:supplier_list": "suppliers.view_supplier",
    "suppliers:purchase_list": "suppliers.view_purchaseorder",
    "sales:sale_list": "sales.view_sale",
    "sales:feedback_list": "sales.view_salefeedback",
    "register:session_list": "register.view_registersession",
    "pricing:tax_list": "pricing.view_tax",
    "pricing:discount_list": "pricing.view_discount",
    "pricing:group_list": "pricing.view_customergroup",
    "expenses:expense_list": "expenses.view_expense",
    "expenses:category_list": "expenses.view_expensecategory",
    "devices:device_list": "devices.view_device",
    "catalog:category_list": "catalog.view_category",
    "catalog:brand_list": "catalog.view_brand",
    "catalog:unit_list": "catalog.view_unit",
}


class ListGuardTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.nobody = get_user_model().objects.create_user("nobody", "n@example.com", PASSWORD, store=self.w.store)

    def test_anonymous_goes_to_login(self):
        for name in LISTS:
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn("login", resp["Location"], name)

    def test_user_without_the_view_permission_gets_403(self):
        self.client.login(username="nobody", password=PASSWORD)
        for name in LISTS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)

    def test_view_permission_opens_exactly_that_list(self):
        for name, perm in LISTS.items():
            user = get_user_model().objects.create_user(f"u{abs(hash(name)) % 10**6}", "x@example.com", PASSWORD, store=self.w.store)
            grant(user, perm, *(["sales.view_sale"] if name == "sales:feedback_list" else []))   # feedback also needs view_sale
            self.client.login(username=user.username, password=PASSWORD)
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_super_admin_opens_every_list(self):
        self.client.login(username="manager", password=PASSWORD)
        for name in LISTS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_pos_helpers_need_a_permission_too(self):
        self.client.login(username="nobody", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("customers:customer_search")).status_code, 403)
        self.assertEqual(self.client.get(reverse("catalog:api_variant_lookup")).status_code, 403)
        self.assertEqual(self.client.get(reverse("register:summary")).status_code, 403)
        self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("customers:customer_search")).status_code, 200)
        self.assertEqual(self.client.get(reverse("catalog:api_variant_lookup")).status_code, 200)

