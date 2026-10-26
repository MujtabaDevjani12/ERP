"""
manage.py seed_demo

Creates one demo store with users/roles, catalog, tax, stock and a received
purchase order so a fresh checkout of this project already has something to
click on — log in and the POS screen isn't empty. Safe to re-run: every
lookup is get_or_create, nothing is duplicated.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from catalog.models import Brand, Category, Product, Unit
from configuration.models import POSConfiguration, SystemConfiguration
from customers.models import Customer
from expenses.models import ExpenseCategory
from inventory.models import StockItem
from pricing.models import CustomerGroup, Tax
from register.models import Register
from suppliers.models import PurchaseOrder, PurchaseOrderItem, Supplier
from tenants.models import Store, Warehouse


class Command(BaseCommand):
    help = "Seeds a demo store, users, roles, catalog and opening stock so the app isn't empty on first run."

    def handle(self, *args, **options):
        with transaction.atomic():
            store = self._store()
            warehouse = self._warehouse(store)
            self._system_config(warehouse)
            self._pos_config(store, warehouse)
            manager_group, cashier_group = self._roles()
            self._users(store, manager_group, cashier_group)
            unit_pcs = self._units(store)
            category = self._category(store)
            brand = self._brand(store)
            tax = self._tax(store)
            self._customer_groups(store)
            variants = self._products(store, category, brand, unit_pcs, tax)
            self._stock_in(warehouse, variants)
            self._purchase_order(store, warehouse, variants)
            self._customers(store)
            self._expense_categories(store)
            self._register(store, warehouse)

        self.stdout.write(self.style.SUCCESS("\nDemo data ready."))
        self.stdout.write("Log in with:")
        self.stdout.write("  admin    / admin12345    (superuser / owner)")
        self.stdout.write("  manager  / manager12345  (Manager role)")
        self.stdout.write("  cashier  / cashier12345  (Cashier role, biller)")
        self.stdout.write("Then open a register (sidebar → Registers) before using the POS screen.")

    # ---------------------------------------------------------------- store
    def _store(self):
        store, created = Store.objects.get_or_create(
            name="Haq Mart & Jewellers",
            defaults=dict(
                business_type="mart",
                address="Main Bazaar, Lahore, Pakistan",
                phone="0300-1234567",
                email="store@example.com",
                currency_code="PKR",
                currency_symbol="Rs",
            ),
        )
        self.stdout.write(("Created" if created else "Found") + f" store: {store.name}")
        return store

    def _warehouse(self, store):
        warehouse, _ = Warehouse.objects.get_or_create(store=store, name="Main Warehouse", defaults={"is_default": True})
        return warehouse

    def _system_config(self, warehouse):
        config = SystemConfiguration.get()
        config.site_name = "ERP"
        config.default_email = "store@example.com"
        config.default_warehouse = warehouse
        config.save()

    def _pos_config(self, store, warehouse):
        POSConfiguration.objects.get_or_create(store=store, defaults={"default_warehouse": warehouse})

    # ---------------------------------------------------------------- roles
    def _roles(self):
        manager_group, _ = Group.objects.get_or_create(name="Manager")
        manager_group.permissions.set(
            # "auth" is included on purpose — that's where Group/Permission
            # live, so Manager needs it to use Roles & Users in-app (not
            # just Django admin). "admin"/"sessions"/"contenttypes" are
            # framework bookkeeping no role should need directly.
            Permission.objects.exclude(content_type__app_label__in=["admin", "sessions", "contenttypes"])
        )

        cashier_group, _ = Group.objects.get_or_create(name="Cashier")
        cashier_codenames = [
            "add_sale", "view_sale", "change_sale",
            "add_payment", "view_payment",
            "add_customer", "view_customer", "change_customer",
            "view_product", "view_productvariant",
            "view_stockitem", "add_stockadjustment", "view_stockadjustment",
            "add_expense", "view_expense",
            "add_registersession", "view_registersession", "change_registersession",
        ]
        cashier_group.permissions.set(Permission.objects.filter(codename__in=cashier_codenames))
        self.stdout.write("Roles ready: Manager (broad access), Cashier (POS + basic).")
        return manager_group, cashier_group

    def _users(self, store, manager_group, cashier_group):
        if not User.objects.filter(username="admin").exists():
            admin = User.objects.create_superuser(
                username="admin", email="admin@example.com", password="admin12345",
                first_name="Store", last_name="Owner",
            )
            admin.store = store
            admin.is_biller = True
            admin.save(update_fields=["store", "is_biller"])

        if not User.objects.filter(username="manager").exists():
            manager = User(
                username="manager", email="manager@example.com", first_name="Store", last_name="Manager",
                store=store, is_staff=True, is_biller=True,
            )
            manager.set_password("manager12345")
            manager.save()
            manager.groups.add(manager_group)

        if not User.objects.filter(username="cashier").exists():
            cashier = User(
                username="cashier", email="cashier@example.com", first_name="Front", last_name="Cashier",
                store=store, is_staff=False, is_biller=True,
            )
            cashier.set_password("cashier12345")
            cashier.save()
            cashier.groups.add(cashier_group)

    # ------------------------------------------------------------- catalog
    def _units(self, store):
        unit_pcs, _ = Unit.objects.get_or_create(store=store, name="Piece", defaults={"short_name": "Pcs", "allow_decimal": False})
        Unit.objects.get_or_create(store=store, name="Kilogram", defaults={"short_name": "Kg", "allow_decimal": True})
        return unit_pcs

    def _category(self, store):
        category, _ = Category.objects.get_or_create(store=store, parent=None, name="General")
        return category

    def _brand(self, store):
        brand, _ = Brand.objects.get_or_create(store=store, name="Generic")
        return brand

    def _tax(self, store):
        tax, _ = Tax.objects.get_or_create(
            store=store, name="GST", defaults={"rate": Decimal("17.00"), "applies_to": "both", "is_default": True},
        )
        return tax

    def _customer_groups(self, store):
        CustomerGroup.objects.get_or_create(store=store, name="General", defaults={"price_adjustment_percentage": Decimal("0.00"), "is_default": True})
        CustomerGroup.objects.get_or_create(store=store, name="Wholesale", defaults={"price_adjustment_percentage": Decimal("-10.00")})

    def _products(self, store, category, brand, unit, tax):
        demo_products = [
            ("Sugar 1kg", "SUG-1KG", Decimal("140.00"), Decimal("160.00")),
            ("Cooking Oil 5L", "OIL-5L", Decimal("2200.00"), Decimal("2450.00")),
            ("Rice Basmati 1kg", "RICE-1KG", Decimal("280.00"), Decimal("320.00")),
            ("Tea Pack 400g", "TEA-400G", Decimal("450.00"), Decimal("500.00")),
            ("Soft Drink 1.5L", "DRINK-1.5L", Decimal("140.00"), Decimal("160.00")),
        ]
        variants = []
        for name, sku, cost, price in demo_products:
            product, _ = Product.objects.get_or_create(
                store=store, sku=sku,
                defaults=dict(name=name, category=category, brand=brand, unit=unit, tax=tax, product_type="single", alert_quantity=10),
            )
            # Product.save() already auto-creates a 'Default' variant via the
            # ensure_default_variant signal — just fill in its prices.
            variant = product.variants.first()
            variant.cost_price = cost
            variant.selling_price = price
            variant.save(update_fields=["cost_price", "selling_price", "updated_at"])
            variants.append(variant)
        self.stdout.write(f"{len(variants)} demo products ready.")
        return variants

    def _stock_in(self, warehouse, variants):
        for variant in variants:
            stock_item, created = StockItem.objects.get_or_create(variant=variant, warehouse=warehouse)
            if created or stock_item.quantity == 0:
                stock_item.adjust(Decimal("100"))

    def _purchase_order(self, store, warehouse, variants):
        supplier, _ = Supplier.objects.get_or_create(store=store, name="Al-Falah Traders", defaults={"phone": "042-111222333"})
        po, created = PurchaseOrder.objects.get_or_create(
            reference="PO-DEMO-0001", supplier=supplier, warehouse=warehouse,
            defaults=dict(status="received", order_date=date.today() - timedelta(days=5)),
        )
        if created:
            PurchaseOrderItem.objects.create(purchase_order=po, variant=variants[0], quantity=Decimal("50"), cost_price=variants[0].cost_price)
            stock_item = StockItem.objects.get(variant=variants[0], warehouse=warehouse)
            stock_item.adjust(Decimal("50"))

    def _customers(self, store):
        Customer.objects.get_or_create(store=store, name="Walk-in Customer")
        Customer.objects.get_or_create(
            store=store, name="Ahmed Raza",
            defaults={"phone": "0321-1112222", "cnic": "35201-1234567-1", "email": "ahmed@example.com"},
        )

    def _expense_categories(self, store):
        ExpenseCategory.objects.get_or_create(store=store, name="Utilities")
        ExpenseCategory.objects.get_or_create(store=store, name="Rent")

    def _register(self, store, warehouse):
        Register.objects.get_or_create(store=store, warehouse=warehouse, name="Counter 1")
