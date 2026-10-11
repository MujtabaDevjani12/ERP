"""
manage.py seed_demo_erp

DEVELOPMENT ONLY. Adds a small, clearly fake ERP data set on top of the POS demo (or any dev database):
the ERP role groups, one user per role, three warehouses, raw materials + a finished shirt with a bill of materials,
a supplier, a wholesale customer, opening stock/cash, and two employees.

 * Refuses to run when DEBUG is False (use --force-not-debug only on a throw-away database).
 * Never touches existing users' passwords. New users get a random password that is printed once and stored nowhere
   (or the one you pass with --password).
 * Re-runnable: every row is looked up before it is created.
"""
import secrets
from datetime import date

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import Product, Unit
from company.models import Company
from customers.models import Customer
from masters.models import ItemProfile, PaymentTerm, TaxRate
from masters.services import ensure_customer_profile, ensure_item_profile, ensure_vendor_profile
from suppliers.models import Supplier
from tenants.models import Store, Warehouse

# username -> (role group, first name)
DEMO_USERS = {
    "admin": (None, "Admin"),                       # superuser; created only if missing
    "manager": ("General Manager", "General Manager"),
    "purchase": ("Purchase Manager", "Purchase"),
    "warehouse": ("Warehouse Manager", "Warehouse"),
    "production": ("Production Manager", "Production"),
    "qc": ("QC Manager", "Quality"),
    "sales": ("Sales Manager", "Sales"),
    "cashier": ("POS Cashier", "Cashier"),
    "accountant": ("Accountant", "Accountant"),
    "hr": ("HR Manager", "HR"),
}


class Command(BaseCommand):
    help = "DEV ONLY: seed fake ERP demo data (users per role, warehouses, items, BOM, supplier, customer, employees)."

    def add_arguments(self, parser):
        parser.add_argument("--password", help="Password for NEW demo users (default: random, printed once).")
        parser.add_argument("--force-not-debug", action="store_true", help="Allow running with DEBUG=False (throw-away databases only).")

    def handle(self, *args, password=None, force_not_debug=False, **opts):
        if not settings.DEBUG and not force_not_debug:
            raise CommandError("seed_demo_erp creates fake users and data and must not run on a production database (DEBUG is False). "
                               "Use --force-not-debug only on a throw-away database.")
        company = Company.objects.order_by("id").first()
        store = Store.objects.order_by("id").first()
        if company is None or store is None:
            raise CommandError("No company/branch yet. Run `python manage.py bootstrap_company` (and `seed_demo` for the POS demo) first.")
        if store.company_id is None:
            raise CommandError("The first branch has no company. Run `python manage.py bootstrap_company`.")
        company = store.company

        call_command("seed_chart_of_accounts", company=company.name, verbosity=0)
        call_command("seed_roles", verbosity=0)

        with transaction.atomic():
            passwords = self._users(store, password)
            self._masters(company)
            wh = self._warehouses(store)
            items = self._items(store, company)
            self._opening(company, wh, items)
            self._parties(company, store)
            self._bom(company, items)
            self._employees(company, store)

        self.stdout.write(self.style.SUCCESS("\nERP demo data ready (DEVELOPMENT ONLY - fake data)."))
        if passwords:
            self.stdout.write("New demo users (password shown once, not stored anywhere):")
            for username, pw in passwords.items():
                self.stdout.write(f"  {username:<11} {pw}")
        else:
            self.stdout.write("All demo users already existed; their passwords were not changed.")
        self.stdout.write("Log in at /admin/ (the ERP screens live there) and /erp/ for the dashboard.")

    # ------------------------------------------------------------ users
    def _users(self, store, password):
        User = get_user_model()
        created = {}
        for username, (role, first) in DEMO_USERS.items():
            user = User.objects.filter(username=username).first()
            if user is None:
                pw = password or secrets.token_urlsafe(9)
                if username == "admin":
                    user = User.objects.create_superuser(username=username, email=f"{username}@example.invalid", password=pw)
                else:
                    user = User(username=username, email=f"{username}@example.invalid", first_name=first, is_staff=True)
                    user.set_password(pw)
                created[username] = pw
            user.store = user.store or store
            if username != "admin":
                user.is_staff = True                     # the ERP screens are in the Django admin
            user.save()
            if role:
                user.groups.add(Group.objects.get(name=role))
        return created

    # ------------------------------------------------------------ masters
    def _masters(self, company):
        PaymentTerm.objects.get_or_create(company=company, name="Cash", defaults={"days": 0})
        PaymentTerm.objects.get_or_create(company=company, name="Net 30", defaults={"days": 30})
        TaxRate.objects.get_or_create(company=company, name="Demo sales tax", defaults={"rate": 10, "tax_type": TaxRate.BOTH})

    def _warehouses(self, store):
        out = {}
        for code, name, kind in (("RAW", "Raw material store", "RAW_MATERIAL"), ("WIP", "Production floor (WIP)", "WIP"),
                                 ("FG", "Finished goods store", "FINISHED_GOODS")):
            wh = Warehouse.objects.filter(store=store, code=code).first() or Warehouse.objects.create(store=store, code=code, name=name,
                                                                                                      warehouse_type=kind)
            out[code] = wh
        return out

    def _items(self, store, company):
        unit, _ = Unit.objects.get_or_create(store=store, name="Piece", defaults={"short_name": "Pcs", "allow_decimal": True})
        meter, _ = Unit.objects.get_or_create(store=store, name="Meter", defaults={"short_name": "m", "allow_decimal": True})
        spec = (("DEMO-FABRIC", "Cotton fabric (demo)", meter, ItemProfile.RAW, 150, 0),
                ("DEMO-THREAD", "Sewing thread (demo)", unit, ItemProfile.RAW, 20, 0),
                ("DEMO-SHIRT", "Shirt (demo)", unit, ItemProfile.FINISHED, 0, 1200))
        out = {}
        for sku, name, u, kind, cost, price in spec:
            product = Product.objects.filter(store=store, sku=sku).first() or Product.objects.create(
                store=store, unit=u, name=name, sku=sku, product_type="single")
            variant = product.variants.first()
            changed = False
            if cost and not variant.cost_price:
                variant.cost_price, changed = cost, True
            if price and not variant.selling_price:
                variant.selling_price, changed = price, True
            if changed:
                variant.save()
            ensure_item_profile(variant, item_type=kind)
            out[sku] = variant
        return out

    def _opening(self, company, wh, items):
        from accounting.services import create_journal_entry
        from stock.models import StockLedgerEntry
        from stock.services import post_stock_movement
        if not StockLedgerEntry.objects.filter(reference_no="DEMO-OPEN").exists():
            total = 0
            for sku, qty in (("DEMO-FABRIC", 500), ("DEMO-THREAD", 200)):
                v = items[sku]
                post_stock_movement(v, wh["RAW"], qty, reference_type="OPENING", reference_no="DEMO-OPEN", unit_cost=v.cost_price)
                total += qty * v.cost_price
            create_journal_entry(company, narration="Demo opening stock", source=("demo_opening_stock", "1"), lines=[
                {"account": "raw_inventory", "debit": total}, {"account": "opening_balance_equity", "credit": total}])
        create_journal_entry(company, narration="Demo opening cash", source=("demo_opening_cash", "1"), lines=[
            {"account": "cash", "debit": 500000}, {"account": "opening_balance_equity", "credit": 500000}])

    def _parties(self, company, store):
        term = PaymentTerm.objects.get(company=company, name="Net 30")
        supplier = Supplier.objects.filter(store=store, name="Demo Textile Mills").first() or Supplier.objects.create(
            store=store, name="Demo Textile Mills", phone="0300-0000001")
        ensure_vendor_profile(supplier, company, payment_term=term)
        customer = Customer.objects.filter(store=store, name="Demo Wholesale Traders").first() or Customer.objects.create(
            store=store, name="Demo Wholesale Traders", phone="0300-0000002")
        ensure_customer_profile(customer, company, payment_term=term, credit_limit=200000)

    def _bom(self, company, items):
        from production import services as pr
        from production.models import BOM, Operation
        if BOM.objects.filter(company=company, variant=items["DEMO-SHIRT"]).exists():
            return
        sewing, _ = Operation.objects.get_or_create(company=company, name="Sewing")
        bom = pr.create_bom(company, items["DEMO-SHIRT"], [{"component": items["DEMO-FABRIC"], "quantity": 1.8, "scrap_percent": 5},
                                                          {"component": items["DEMO-THREAD"], "quantity": 1}],
                            operations=[{"operation": sewing, "minutes_per_unit": 25, "labor_rate_per_hour": 300, "overhead_rate_per_hour": 100}])
        pr.activate_bom(bom)

    def _employees(self, company, store):
        from hr.models import Department, Designation, Employee
        from payroll.models import SalaryStructure
        dept, _ = Department.objects.get_or_create(company=company, name="Production")
        desig, _ = Designation.objects.get_or_create(company=company, name="Operator")
        for code, name, basic in (("E001", "Demo Operator One", 40000), ("E002", "Demo Operator Two", 45000)):
            emp = Employee.objects.filter(company=company, employee_code=code).first() or Employee.objects.create(
                company=company, store=store, employee_code=code, full_name=name, joining_date=date(2026, 1, 1), department=dept, designation=desig)
            SalaryStructure.objects.get_or_create(employee=emp, defaults={"basic": basic})
