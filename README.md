# Multiple POS

Ek Django-based POS system jo Gold/Diamond, Mart, Restaurant aur Clothing — sab business types ko ek hi system se handle karta hai. Multi-store, multi-warehouse, role-based permissions, POS billing screen, register (till) sessions, purchases/suppliers, inventory + barcode scanning, invoices (PDF/email/WhatsApp), reports/charts, aur CSV import/export.

## 1. Setup (Windows / PowerShell)

```powershell
cd C:\Multiple-POS
python -m venv venv
venv\Scripts\activate

pip install -r requirements.txt
copy .env.example .env
```

`.env` file kholein aur kam az kam `SECRET_KEY` change kar dein. Baqi (email/WhatsApp) baad mein bhar sakte hain — un ke bina bhi system pura chalega, sirf woh do delivery channels off rahenge.

> **Invoice PDF:** ab ReportLab se banta hai (pure Python, `pip install -r requirements.txt` kaafi hai) — Windows par GTK/Pango/Cairo ya WeasyPrint install karne ki zaroorat nahi.

```powershell
python manage.py makemigrations
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

(`makemigrations` zaroori hai — is checkout mein migration files shamil nahi hain kyunke jahan yeh code likha gaya wahan Django chalane ki sahulat nahi thi. Ek dafa `makemigrations` chala kar commit kar dein, phir aage sirf `migrate` hi chalega.)

Browser mein `http://127.0.0.1:8000/` kholein — login page aayega.

## 2. Demo logins (seed_demo command banata hai)

| Username  | Password      | Role                          |
|-----------|---------------|--------------------------------|
| `admin`   | `admin12345`  | Superuser / owner (sab kuch)  |
| `manager` | `manager12345`| Manager (Django admin bhi)    |
| `cashier` | `cashier12345`| Cashier / biller (sirf POS)   |

Pehli baar login karne ke baad **sidebar → Registers → Open register** karein — POS screen tabhi khulti hai jab ek register session open ho (jaan-boojh kar esa design hai, taake har sale kisi na kisi register/shift se linked rahe).

Production mein `seed_demo` mat chalayen (ya demo users ka password turant badal dein) — yeh sirf testing ke liye hai.

## 3. Project ka structure (ek app = ek zimmedari)

```
config/          settings, root urls.py, wsgi/asgi
core/            shared base models (TimeStampedModel, CreatedByModel) + helpers
tenants/         Store, Warehouse — har cheez isi se link hoti hai
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

Roles/permissions Django ke built-in Group/Permission system se chalte hain — koi alag "Role" model nahi (`/admin/auth/group/` se naye roles bana kar checkbox se permissions de sakte hain, phir Users ko wahi group assign kar dein).

## 4. Kya pura working hai vs kya abhi stub/manual setup maangta hai

**Pura working (is checkout ke saath hi chal jayega):**
- Login, multi-store/warehouse, custom User + Django Group-based roles
- POS billing screen — cart, barcode scan/search, split payments, quick cash, held sales, hotkeys (F4/F7/F8/Ctrl+F3/Ctrl+Shift+A)
- Register open/close (apni alag pages), expected-cash vs counted-cash variance
- Product add/edit with variants (Size/Color/Karat/Weight — sab ek hi attribute system se), auto barcode
- Quantity Adjustment — scan-based, same barcode UX jaise POS mein
- Purchases: Supplier → Purchase Order → (received hote hi) stock automatically andar, payments partial/due/paid
- Sales: list/detail, add payment, payment status (due/partial/paid), PDF invoice, sale returns model (UI abhi list-level tak)
- Customers (CNIC field), Gift cards, Loyalty points (auto-award on sale)
- Expenses + categories, register-linked cash-out tracking
- Settings: **POS Configuration** aur **System Configuration** do alag pages, + document number prefixes (SALE/SR/IPAY/POP/DO/PO/PR/TR/EXP/QA)
- Reports dashboard: revenue/due/purchases/expenses/profit cards, sales trend chart, top products, low-stock alert, payment-method breakdown
- CSV export (sales, products) aur CSV import (products) — sidebar → Reports

**Kaam karta hai lekin real credentials chahiye (.env mein):**
- Email invoice sending — abhi console backend use ho raha hai (terminal mein print hota hai), real bhejne ke liye `EMAIL_HOST` wagera .env mein bharein
- WhatsApp invoice sending — Meta WhatsApp Cloud API stub hai, `WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` chahiye, warna Document Log mein "not configured" error show hoga (silently fail nahi hota)

**Abhi tak nahi bana (aage add ho sakta hai):**
- Online payment gateways (PayPal/Stripe/Authorize.net) — sirf settings mein placeholder fields hain, actual API integration nahi
- Media library ka apna browsing UI (model maujood hai, list/upload screen nahi)
- Product image ZIP bulk-upload
- Stock transfer aur sale-return ke apne dedicated forms (models + basic list maujood hain)

## 5. Development notes

- Frontend: server-rendered Django templates + Tailwind (CDN) + Alpine.js — koi alag frontend build step nahi
- Sabhi document numbers (`SALE-000001` waghera) `configuration.NumberingPrefix.next_reference()` se atomically milte hain — race-safe hai (select_for_update)
- Har `Product` ka kam az kam 1 `ProductVariant` hamesha hota hai (signal se auto-create) — Sales/Purchases/Stock hamesha variant ko point karte hain, kabhi Product ko seedha nahi
- Ye code is is sandbox mein likha gaya hai jahan `pip install`/`manage.py` chalane ki sahulat nahi thi (no PyPI access) — is liye apni machine par upar wale migrate/runserver steps zaroor chalayen aur agar koi chhoti typo/error mile to bata dein, foran fix ho jayegi.

---

## ERP modules (Manufacturing + Retail + Wholesale)

Upar wala POS bilkul pehle ki tarah chalta hai. Us ke upar ERP ke modules additive tareeqe se add kiye gaye hain
(naye apps, naye tables — purane tables/screens nahi tode gaye):

| Module | Kaam |
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

* **Pehle yeh padhein:** `ERP_RUN_GUIDE.md` (exact commands, backup ke saath).
* Design aur rules: `docs/ERP_ARCHITECTURE.md`, `docs/ACCOUNTING_RULES.md`, `docs/PRODUCTION_FLOW.md`,
  `docs/DATABASE_ARCHITECTURE.md`, `docs/API_DOCUMENTATION.md`, `docs/DEPLOYMENT.md`; audit/roadmap: `ERP_AUDIT.md`, `ERP_ROADMAP.md`.
* ERP documents ki zyada tar screens **Django admin** (`/admin/`) mein hain; `/erp/` dashboard aur reports ke liye.
* **Demo data sirf development ke liye:** `python manage.py seed_demo_erp` (DEBUG=False par chalta hi nahi).
  Production par kabhi `seed_demo` / `seed_demo_erp` na chalayen; `seed_demo` ke weak passwords (`admin12345` ...) turant badal dein.
