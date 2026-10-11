"""Shared fixtures for the POS tests (not a test module itself - nothing here runs on its own)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

from catalog.models import Product, ProductVariant, Unit
from company.models import Company
from inventory.models import StockItem
from payments.models import PaymentMethod
from pricing.models import Tax
from register.models import Register
from tenants.models import Store, Warehouse

PASSWORD = "pw-12345-x"


def grant(user, *codes):
    """grant(user, "sales.add_sale", ...) - Django model permissions by 'app.codename'."""
    for code in codes:
        app, name = code.split(".")
        user.user_permissions.add(Permission.objects.get(content_type__app_label=app, codename=name))
    # drop the permission cache Django keeps on the instance
    for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        if hasattr(user, attr):
            delattr(user, attr)


CASHIER_PERMS = ["sales.add_sale", "sales.view_sale", "payments.add_payment", "sales.add_salereturn"]


class PosWorld:
    """One company/branch with a warehouse, two counters, a manager, a cashier and a few products."""

    def __init__(self):
        User = get_user_model()
        self.company = Company.objects.create(name="Acme")
        self.store = Store.objects.create(name="Main", company=self.company)
        self.warehouse = Warehouse.objects.create(store=self.store, name="Floor", is_default=True)
        self.unit = Unit.objects.create(store=self.store, name="Piece", short_name="pc")
        self.tax = Tax.objects.create(store=self.store, name="GST", rate=Decimal("10"))
        self.counter1 = Register.objects.create(store=self.store, warehouse=self.warehouse, name="Counter 1", code="C1")
        self.counter2 = Register.objects.create(store=self.store, warehouse=self.warehouse, name="Counter 2", code="C2")
        self.manager = User.objects.create_superuser("manager", "m@example.com", PASSWORD, store=self.store)
        self.cashier = User.objects.create_user("cashier", "c@example.com", PASSWORD, store=self.store, is_biller=True)
        self.cashier2 = User.objects.create_user("cashier2", "c2@example.com", PASSWORD, store=self.store, is_biller=True)
        grant(self.cashier, *CASHIER_PERMS)
        grant(self.cashier2, *CASHIER_PERMS)
        PaymentMethod.ensure_defaults(self.store)
        self.tea = self.product("Tea", "TEA", price="100", stock="10", tax=True)
        self.milk = self.product("Milk", "MILK", price="50", stock="5")
        self.empty = self.product("Empty shelf", "EMPTY", price="30", stock="0")

    def product(self, name, sku, *, price, stock, tax=False):
        product = Product(store=self.store, unit=self.unit, name=name, sku=sku, tax=self.tax if tax else None)
        product._skip_default_variant = True            # this fixture adds its own variant below (no stray "Default" one)
        product.save()
        variant = ProductVariant.objects.create(product=product, sku=f"{sku}-1", barcode=f"BC{sku}", selling_price=Decimal(price), cost_price=Decimal("10"))
        StockItem.objects.create(variant=variant, warehouse=self.warehouse, quantity=Decimal(stock))
        return variant

    def stock_of(self, variant):
        return StockItem.objects.get(variant=variant, warehouse=self.warehouse).quantity
