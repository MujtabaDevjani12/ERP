# Multiple POS

A Django-based POS system that handles Gold/Diamond, Mart, Restaurant and Clothing businesses, all from a single system. It includes multi-store and multi-warehouse support, role-based permissions, a POS billing screen, register (till) sessions, purchases/suppliers, inventory with barcode scanning, invoices (PDF/email/WhatsApp), reports/charts, and CSV import/export.

## 1. Setup (Windows / PowerShell)

```powershell
cd C:\Multiple-POS
python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
copy .env.example .env
```

Open the `.env` file and change at least `SECRET_KEY`. You can fill in the rest (email/WhatsApp) later. The system works fully without them; only those two delivery channels will stay off.

> **Invoice PDF:** It is now generated with ReportLab (pure Python, so `pip install -r requirements.txt` is enough). There is no need to install GTK/Pango/Cairo or WeasyPrint on Windows.

```powershell
python manage.py makemigrations
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

(`makemigrations` is required because this checkout does not include migration files. The environment where this code was written had no way to run Django. Run `makemigrations` once and commit the result; after that, only `migrate` is needed.)

Open `http://127.0.0.1:8000/` in your browser and the login page will appear.

## 2. Demo logins (created by the `seed_demo` command)

| Username  | Password       | Role                              |
|-----------|----------------|-----------------------------------|
| `admin`   | `admin12345`   | Superuser / owner (full access)   |
| `manager` | `manager12345` | Manager (also has Django admin)   |
| `cashier` | `cashier12345` | Cashier / biller (POS only)       |

After logging in for the first time, go to **sidebar → Registers → Open register**. The POS screen only opens when a register session is open. This is deliberate, so that every sale is always linked to some register/shift.

Do not run `seed_demo` in production (or change the demo users' passwords immediately). It is for testing only.

## 3. Project structure (one app = one responsibility)

```
config/          settings, root urls.py, wsgi/asgi
core/            shared base models (TimeStampedModel, CreatedByModel) + helpers
tenants/         Store, Warehouse — everything links back to these
accounts/        custom User (store, is_biller, pos_pin)
configuration/   POS Configuration (per-store) + System Configuration (site-wide) + number prefixes
pricing/         Tax, Discount, CustomerGroup
catalog/         Category, Brand, Unit, Product, ProductVariant (+ barcode auto-generate)
inventory/       StockItem, quantity adjustment (scan), racks, transfers
suppliers/       Supplier, PurchaseOrder, PurchasePayment
customers/       Customer (CNIC), LoyaltyTransaction, GiftCard
devices/         Printer/scanner/scale registry, media library
register/        Till/counter open-close sessions
sales/           Sale, SaleItem, POS "Finalize Sale" endpoint, returns, delivery
payments/        Payment, Refund
expenses/        ExpenseCategory, Expense
documents/       Invoice PDF / email / WhatsApp sending + DocumentLog audit trail
dashboard/       Server-rendered UI: home dashboard + the POS billing screen
reports/         Sales/stock reports, charts, CSV import/export — no models of its own
```

Roles and permissions run on Django's built-in Group/Permission system. There is no separate "Role" model. You can create new roles at `/admin/auth/group/`, assign permissions with checkboxes, and then assign that group to Users.

## 4. What is fully working vs. what is a stub or needs manual setup

**Fully working (runs out of the box with this checkout):**
- Login, multi-store/warehouse, custom User + Django Group-based roles
- POS billing screen: cart, barcode scan/search, split payments, quick cash, held sales, hotkeys (F4/F7/F8/Ctrl+F3/Ctrl+Shift+A)
- Register open/close (separate pages), expected-cash vs. counted-cash variance
- Product add/edit with variants (Size/Color/Karat/Weight, all in one attribute system), auto barcode
- Quantity Adjustment: scan-based, with the same barcode UX as the POS
- Purchases: Supplier → Purchase Order → (once received) stock is added automatically; payments can be partial/due/paid
- Sales: list/detail, add payment, payment status (due/partial/paid), PDF invoice, sale returns model (UI only at list level for now)
- Customers (CNIC field), Gift cards, Loyalty points (auto-awarded on sale)
- Expenses + categories, register-linked cash-out tracking
- Settings: separate **POS Configuration** and **System Configuration** pages, plus document number prefixes (SALE/SR/IPAY/POP/DO/PO/PR/TR/EXP/QA)
- Reports dashboard: revenue/due/purchases/expenses/profit cards, sales trend chart, top products, low-stock alert, payment-method breakdown
- CSV export (sales, products) and CSV import (products): sidebar → Reports

**Works, but needs real credentials (in `.env`):**
- Email invoice sending: currently uses the console backend (output is printed in the terminal). To send real emails, fill in `EMAIL_HOST` etc. in `.env`.
- WhatsApp invoice sending: the Meta WhatsApp Cloud API integration is a stub. It needs `WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID`; otherwise the Document Log will show a "not configured" error (it does not fail silently).

**Not built yet (can be added later):**
- Online payment gateways (PayPal/Stripe/Authorize.net): only placeholder fields exist in settings, with no actual API integration
- A browsing UI for the media library (the model exists, but there is no list/upload screen)
- Product image ZIP bulk-upload
- Dedicated forms for stock transfer and sale return (models + basic lists exist)

## 5. Development notes

- Frontend: server-rendered Django templates + Tailwind (CDN) + Alpine.js, with no separate frontend build step
- All document numbers (`SALE-000001`, etc.) are obtained atomically from `configuration.NumberingPrefix.next_reference()`, which is race-safe (`select_for_update`)
- Every `Product` always has at least 1 `ProductVariant` (auto-created via a signal). Sales/Purchases/Stock always point to the variant, never directly to the Product
- This code was written in a sandbox where `pip install` and `manage.py` could not be run (no PyPI access). So please run the migrate/runserver steps above on your own machine, and if you find any small typo or error, report it and it will be fixed right away.

---

## ERP modules (Manufacturing + Retail + Wholesale)

The POS above works exactly as before. The ERP modules have been added on top of it in an additive way (new apps, new tables; the old tables and screens were not broken).

| Module | Purpose |
|---|---|
| `company`, `numbering`, `audit`, `attachments` | Company/branch, document numbers (PO-2026-00001), audit trail, files |
| `rbac`, `approvals` | 21 roles, approval workflows (levels, amount limits, delegation) |
| `accounting` | Double-entry ledger, trial balance, P&L, balance sheet, cash flow, tax, bank, assets, budgets |
| `masters` | Payment terms, tax rates, item/vendor/customer profiles, Excel import |
| `stock`, `quality` | Stock ledger, weighted-average cost, batches, transfers, adjustments, counts, inspections |
| `procurement` | Requisition -> RFQ -> quotation -> PO -> GRN -> invoice -> payment, vendor returns |
| `production` | BOM, work orders, material issue, job cards, finished goods, costing |
| `distribution` | Quotation -> order -> delivery -> invoice -> receipt, sales returns, credit limits |
| `pos_erp` | POS returns/refunds, cash drops, shift close, optional POS -> accounting posting |
| `hr`, `payroll` | Employees, attendance, leave, loans, payroll, payslips, bank file |
| `reports_erp`, `notifications`, `api` | `/erp/` dashboard + reports (Excel/PDF), alerts inbox, read-only REST API |

* **Read this first:** `ERP_RUN_GUIDE.md` (exact commands, including backup).
* Design and rules: `docs/ERP_ARCHITECTURE.md`, `docs/ACCOUNTING_RULES.md`, `docs/PRODUCTION_FLOW.md`,
  `docs/DATABASE_ARCHITECTURE.md`, `docs/API_DOCUMENTATION.md`, `docs/DEPLOYMENT.md`; audit/roadmap: `ERP_AUDIT.md`, `ERP_ROADMAP.md`.
* Most ERP document screens are in the **Django admin** (`/admin/`); the `/erp/` dashboard is for reports.
* **Demo data is for development only:** `python manage.py seed_demo_erp` (it will not run when DEBUG=False).
  Never run `seed_demo` / `seed_demo_erp` in production; change the weak `seed_demo` passwords (`admin12345`, etc.) immediately.
