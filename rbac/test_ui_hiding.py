"""A user who may only VIEW a screen must not be shown Add / Edit / Delete controls on it (and vice-versa for full access)."""
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld, grant

# list url name -> the view permission (the user in these tests holds ONLY this one)
SCREENS = {
    "catalog:product_list": "catalog.view_product",
    "customers:customer_list": "customers.view_customer",
    "customers:giftcard_list": "customers.view_giftcard",
    "suppliers:supplier_list": "suppliers.view_supplier",
    "suppliers:purchase_list": "suppliers.view_purchaseorder",
    "pricing:tax_list": "pricing.view_tax",
    "pricing:discount_list": "pricing.view_discount",
    "pricing:group_list": "pricing.view_customergroup",
    "expenses:expense_list": "expenses.view_expense",
    "expenses:category_list": "expenses.view_expensecategory",
    "catalog:category_list": "catalog.view_category",
    "catalog:brand_list": "catalog.view_brand",
    "catalog:unit_list": "catalog.view_unit",
    "catalog:attribute_list": "catalog.view_productattribute",
    "inventory:rack_list": "inventory.view_rack",
    "inventory:warehouse_list": "tenants.view_warehouse",
    "inventory:stock_list": "inventory.view_stockitem",
    "inventory:adjustment_list": "inventory.view_stockadjustment",
    "register:counter_list": "register.view_register",
    "register:session_list": "register.view_registersession",
    "payments:method_list": "payments.view_paymentmethod",
    "sales:sale_list": "sales.view_sale",
    "sales:delivery_list": "sales.view_delivery",
    "devices:device_list": "devices.view_device",
    "configuration:currency_list": "configuration.view_currency",
}
WRITE_LINK = re.compile(r"""(?:href|data-drawer-url|data-href|action)="[^"]*/(?:add|edit|delete|new|adjust|quantity-adjustment|open)[^"]*\"""")


def main_html(resp):
    html = resp.content.decode()
    start = html.find('<main id="main"')
    end = html.find("</main>", start)
    return html[start:end]


class ViewOnlyScreensTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        User = get_user_model()
        self.viewers = {}
        for name, perm in SCREENS.items():
            u = User.objects.create_user(f"v{len(self.viewers)}", "v@example.com", PASSWORD, store=self.w.store)
            grant(u, perm, *(["sales.view_sale"] if name == "sales:feedback_list" else []))
            self.viewers[name] = u

    def test_view_only_user_sees_no_write_controls(self):
        for name, user in self.viewers.items():
            self.client.logout()
            self.client.login(username=user.username, password=PASSWORD)
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 200, name)
            found = WRITE_LINK.findall(main_html(resp))
            self.assertEqual(found, [], f"{name} shows write controls to a view-only user: {found[:3]}")
            self.assertNotIn("is-clickable", main_html(resp).split("<tbody")[-1] if "<tbody" in main_html(resp) else "", name)

    def test_full_access_user_still_sees_them(self):
        self.client.login(username="manager", password=PASSWORD)             # Super Admin
        seen = 0
        for name in ("catalog:product_list", "customers:customer_list", "suppliers:supplier_list", "pricing:tax_list", "register:counter_list"):
            if WRITE_LINK.search(main_html(self.client.get(reverse(name)))):
                seen += 1
        self.assertGreaterEqual(seen, 4)
