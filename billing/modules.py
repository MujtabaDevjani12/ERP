"""
The sellable modules. A plan lists which of these a company gets; anything not listed here (dashboard, settings, users,
notifications, billing itself ...) is part of the core product and always available.

key -> (label, short description, [app labels that belong to it])
"""
MODULES = {
    "pos": ("POS & sales", "POS terminal, registers, sales, customers, gift cards, payment methods", ["sales", "customers", "register", "payments", "documents", "pos_erp"]),
    "catalog": ("Catalog & pricing", "Products, categories, brands, units, taxes, discounts", ["catalog", "pricing"]),
    "inventory": ("Inventory & warehouses", "Stock levels, transfers, adjustments, warehouses, racks", ["inventory", "stock"]),
    "purchasing": ("Purchasing", "Suppliers, purchase orders, RFQs, goods receipt", ["suppliers", "procurement"]),
    "distribution": ("B2B sales & distribution", "Quotations, sales invoices, receipts, returns, receivables", ["distribution"]),
    "accounting": ("Accounting & expenses", "Chart of accounts, vouchers, cheques, periods, budgets, expenses", ["accounting", "expenses"]),
    "hr": ("HR", "Employees, attendance, leave, recruitment, training, appraisals", ["hr"]),
    "payroll": ("Payroll", "Salary structures, payroll runs, payslips", ["payroll"]),
    "production": ("Production", "Bills of material, work orders, job cards", ["production"]),
    "quality": ("Quality control", "Inspections and QC plans", ["quality"]),
    "reports": ("Reports & analytics", "Sales, stock, accounting and ERP reports, Excel/PDF export", ["reports", "reports_erp"]),
}

APP_TO_MODULE = {app: key for key, (_, _, apps) in MODULES.items() for app in apps}
ALL_MODULE_KEYS = list(MODULES)


def module_for_app(app_label):
    """The sellable module an app belongs to, or None when it is part of the always-on core."""
    return APP_TO_MODULE.get(app_label)


def module_choices():
    return [(k, v[0]) for k, v in MODULES.items()]
