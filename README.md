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
| `rbac`, `approvals` | 23 roles (Super Admin, Company Admin + 21 job roles), approval workflows (levels, amount limits, delegation) |
| `accounting` | Double-entry ledger, trial balance, P&L, balance sheet, cash flow, tax, bank, assets, budgets, cheque register, fiscal years (close/lock), recurring journals |
| `masters` | Payment terms, tax rates, item/vendor/customer profiles, Excel import |
| `stock`, `quality` | Stock ledger, weighted-average cost, batches, transfers, adjustments, counts, inspections |
| `procurement` | Requisition -> RFQ -> quotation -> PO -> GRN -> invoice -> payment, vendor returns |
| `production` | BOM, work orders, material issue, job cards, finished goods, costing |
| `distribution` | Quotation -> order -> delivery -> invoice -> receipt, sales returns, credit limits |
| `pos_erp` | POS returns/refunds, cash drops, shift close, optional POS -> accounting posting |
| `hr`, `payroll` | Employees (+ documents, promotions/transfers, disciplinary), attendance, leave, loans, recruitment, training, payroll, payslips, bank file |
| `reports_erp`, `notifications`, `api` | `/erp/` dashboard + reports (Excel/PDF), alerts inbox, read-only REST API |

* **Pehle yeh padhein:** `ERP_RUN_GUIDE.md` (exact commands, backup ke saath).
* Design aur rules: `docs/ERP_ARCHITECTURE.md`, `docs/ACCOUNTING_RULES.md`, `docs/PRODUCTION_FLOW.md`,
  `docs/DATABASE_ARCHITECTURE.md`, `docs/API_DOCUMENTATION.md`, `docs/DEPLOYMENT.md`; audit/roadmap: `ERP_AUDIT.md`, `ERP_ROADMAP.md`.
* ERP documents ki zyada tar screens **Django admin** (`/admin/`) mein hain; `/erp/` dashboard aur reports ke liye.
* **Demo data sirf development ke liye:** `python manage.py seed_demo_erp` (DEBUG=False par chalta hi nahi).
  Production par kabhi `seed_demo` / `seed_demo_erp` na chalayen; `seed_demo` ke weak passwords (`admin12345` ...) turant badal dein.


---

## Roles, permissions aur Super Admin

* **Role = Django Group.** `python manage.py seed_roles` 23 roles banata/update karta hai (Super Admin, Company Admin aur 21 job roles) (Auditor = sirf dekh sakta hai). `--reset` har built-in role ko `rbac/roles.py` ke bilkul barabar kar deta hai.
* **Super Admin** = har screen aur har action ka access (naye models ke permissions bhi khud-ba-khud). Do tareeqe: Staff users > user kholein > *Super Admin* tick karein, ya command:
  `python manage.py promote_super_admin <username>` (naya banane ke liye `--create --password ...`; wapas lene ke liye `--demote`).
  `migrate` ke baad Super Admin role ko tamam permissions mil jati hain; jis user ka `is_superuser` hai wo role mein khud shamil ho jata hai.
* **Role screen** (Settings > Roles & permissions): har screen ki row, columns View / Add / Change / Delete + extra actions (approve, post, print ...). Role duplicate bhi ho sakta hai.
* **Hifazati rules:** jis ke paas Super Admin nahi wo kisi ko Super Admin nahi bana sakta; wo sirf wahi permissions/roles de sakta hai jo khud uske paas hain; aakhri active Super Admin delete/deactivate/demote nahi ho sakta; naye user ka password zaroori hai.
* Sidebar, Ctrl+K palette aur dashboard ke numbers ab role ke mutabiq chhup jate hain (jis ka access nahi us ka link nazar hi nahi aata).
* **Buttons bhi role ke mutabiq:** list/detail pages par *Add* sirf us ko dikhta hai jis ke paas add ki permission ho, *Edit* sirf change wale ko, *Delete* sirf delete wale ko; jis ke paas view ki permission nahi us ke liye wo page/menu hi band hai (URL khol kar bhi 403). Yahi rule server par bhi lagta hai, sirf button chhupana kaafi nahi samjha gaya.
* **Recurring journals** cron/Task Scheduler se: `python manage.py run_recurring_journals` (roz chalana safe hai, double posting nahi hoti).


---

## Subscriptions, plans aur payments (SaaS - ERP ke malik ke liye)

Is ERP ko aap alag alag companies ko monthly ya yearly subscription par de sakte hain. Do alag websites hain:

| | ERP (customers) | Owner console (sirf aap) |
|---|---|---|
| Address | `http://127.0.0.1:8000` | `http://127.0.0.1:8001` |
| Login | ERP ke users (username + password, ya Google) | Alag **OwnerAccount** (e-mail + password), ERP login se koi taalluq nahi |
| Database | `db.sqlite3` | `owner.sqlite3` (alag file) |
| Start | `python manage.py runserver` | `python manage.py runserver 8001 --settings=config.owner_settings` (ya `start_owner_console.bat`) |

**Pehli dafa setup**

```powershell
python manage.py migrate
python manage.py seed_roles                      # "Company Admin" role bhi banata hai
python manage.py setup_owner --email aap@example.com --name "Aap Ka Naam" --seed-plans
# password pooch lega (kam az kam 10 characters). Purane /platform/ ka data ho to wo khud import ho jata hai.
python manage.py runserver 8001 --settings=config.owner_settings
```

Phir `http://127.0.0.1:8001/` kholein aur sign in karein. Aur owner accounts: `python manage.py create_owner naya@example.com --name "Naam"` ya console ke **Team** page se (invitation e-mail jati hai). `create_owner` ko dobara chalane se us account ka password reset ho jata hai.

**Owner console ke pages**

| Page | Kya karta hai |
|---|---|
| Dashboard | Greeting + **"Needs your attention"** list (payments to review, overdue, ending soon, unpaid sign-ups, failed e-mails), MRR/ARR + average per company, is mahine ki collection **pichhle mahine se taqabul** (%), unpaid invoices aur payment rate, 12 mahine ka revenue chart (khali ho to wazahat), **company status donut**, plans in use, 8 haftay ke sign-ups, best customers, activity timeline, setup checklist |
| Companies | Har company ka plan, status, bache din, users; company page se plan/price/limits badlein, **free demo din** dein, paid din add karein, suspend/unsuspend, invoice banayein/mark paid/cancel. Jin purani companies ka subscription nahi unhein yahin se start karein |
| Plans | Plan banayein/edit karein: monthly aur yearly price, kaun se modules, users / branches / products / mahine ki sales ki limit (0 = unlimited), features list |
| Payments | Bank / JazzCash / Easypaisa ki receipts dekh kar approve ya reject (reject par reason company ko jata hai) |
| Invoices, Activity | Tamam invoices (search/filter/print) aur har cheez ka audit trail |
| E-mail log | Har bheji hui (ya fail hui) e-mail, status aur error ke saath |
| Team | Owner accounts: Owner (full access) ya Support (sirf companies/payments/invoices dekhna aur payment review) |
| Settings / E-mail settings | Section jump bar ke saath: Business, demo/grace/reminders, **Invoices (prefix + due days)**, Stripe, bank/JazzCash/Easypaisa, **Google Client ID + Secret**, **Announcement banner** (har company ke ERP mein ek message), **sign-up page ke Terms/Privacy links + welcome note**, **Security (sign-in kitni der chale)**, **Data export (Companies / Invoices / Payments CSV)**, aur SMTP e-mail (test message ke saath) |
| My account | Apna profile aur password |

**Sign in / Sign up (ERP login page)**

* Login page par ab **Sign in | Sign up** tabs hain (purana "Create your company - free trial" hat gaya). Sign up par company naam, e-mail, password aur plan chun kar (ya **Google** se) account banta hai.
* Account banne ke baad ERP **band** rehta hai: har page sirf *Billing* par bhejta hai. Pehla invoice khud ban jata hai; Stripe se card payment ya bank/JazzCash/Easypaisa receipt upload karein. Owner receipt approve karta hai to ERP usi waqt khul jata hai.
* Agar aap kisi company ko demo dena chahte hain to Companies > us company > *Give free demo days*. Platform-wide default Settings mein `Trial days` (0 = kisi ko free nahi). Demo khatam hote hi (bina payment) account lock; **paid** period khatam hone par `grace` din chalta hai phir lock.
* Sidebar > *Billing* (`/billing/`): plan, bache din, usage bars, **mahine ke hisaab se invoice history**, plan upgrade/downgrade, "renew na karein". Expiry se pehle (7, 3, 1 din) ERP mein notification + banner + e-mail aati hai.
* **Plan card (Claude jaisa):** sidebar ke neeche aur upar user menu mein ab plan ka naam, status (Active / Free demo / Payment due...), bache din ki bar, plan limits ki mini bars aur **Upgrade plan** (ya payment baqi ho to **Pay now**) button hai. Sirf wo log dekhte hain jinke paas Billing ki permission ho.
* Plan mein jo module nahi, wo sidebar se chhup jata hai aur URL khol kar bhi "Is plan mein shamil nahi" page aata hai.

**Forgot password aur e-mail**

* Owner login par *Forgot password?* link, e-mail mein ek martaba chalne wala link (2 ghante). Naya password kam az kam 10 characters. Password badalte hi baaqi browsers sign out ho jate hain; 5 ghalat koshishon ke baad account 15 minute lock.
* E-mail ke liye Owner console > Settings > E-mail mein SMTP daalein (Gmail ke liye `smtp.gmail.com`, port 587, TLS, aur **App password**), *Send test* dabayein. SMTP na ho to e-mail server ke console mein print hoti hai (development).
* Company ko welcome, invoice, payment received/rejected, reminders aur suspension ki e-mail jati hai; aapko naye sign-up aur nayi receipt ki. Dono Settings mein band ho sakti hain.

**Rozana job** (renewal invoice banata hai, reminders bhejta hai, expired ko lock karta hai):

```powershell
python manage.py run_billing_cycle
```

Windows Task Scheduler mein roz ek dafa. Billing page kholne par bhi company ka status refresh hota hai, aur dashboard par *Run billing check* button bhi hai.

**Gateway setup**

* **Stripe:** Settings mein publishable aur secret key daalein, *Stripe on* karein. Stripe dashboard mein webhook: `https://<ERP-ka-domain>/billing/webhooks/stripe/`, event `checkout.session.completed`, signing secret (`whsec_...`) Settings mein.
* **Google login:** Google Cloud Console > Credentials > OAuth client ID (Web application), Authorised JavaScript origins mein ERP ka domain, phir Client ID (aur Secret) Settings mein. Login/Sign up page par Google button khud aa jata hai.
* **Manual payment:** bank account / JazzCash / Easypaisa number Settings mein; company receipt upload karti hai, aap Payments page par approve karte hain.

**Dhyan dein**

* Owner console ek **alag process** hai - ERP ke saath usay bhi chalana hoga (production mein dono ko gunicorn/waitress se alag ports par aur owner console ko sirf apne private address par rakhein). Environment: `OWNER_DB_PATH` (owner database ki jagah), `OWNER_DB_SAME=True` (agar alag file nahi chahiye), `OWNER_CONSOLE_URL` (e-mail links ke liye).
* Owner database na mile to ERP band nahi hota (billing limits us waqt lagu nahi hoti) - isliye `setup_owner` chalana na bhoolein.
* Data alag rakhna (ek company doosri ka data na dekhe) tab active hota hai jab database mein **1 se zyada company** ho. Ek company wali purani install bilkul pehle jaisi chalti hai.
* Roles (groups) sab companies mein shared hain, isliye jab 1 se zyada company ho to role banana/badalna sirf ERP Super Admin kar sakta hai; Company Admin apne staff ko maujooda roles assign kar sakta hai.
* Jin companies ke paas subscription nahi (purane customers) un par koi limit ya lock nahi lagta. Unhein Owner console > Companies se subscription de sakte hain.
* ERP ka apna Super Admin (jaise `shahwaiz`) ab owner console nahi kholta; owner console sirf OwnerAccount se khulta hai.

---

## Catalog: codes, delete aur bulk delete, description editor

* **Category aur Brand code:** har Category/Brand ka apna code hota hai (jaise `GRO-001`). Khali chhorein to khud ban jata hai; apna likhein to store mein unique hona chahiye. `migrate` purane records ke codes khud bhar deta hai (`catalog 0004`).
* **Single + bulk delete:** Products, Categories, Brands, Units aur baqi simple lists mein har row ka delete icon hai aur checkbox se select karke *Delete selected* chalta hai. Delete se pehle alag page nahi khulta - same page par popup "Are you sure?" aata hai (`static/js/confirm-delete.js`, purana `confirmDialog` istemal hota hai). Jis product ki sales/stock history ho use delete nahi karte - "Mark it inactive instead" ka message aata hai.
* **Attributes:** bulk delete bhi hai. Jo attribute variants mein use ho raha ho uski row par *Delete anyway* button aata hai - wo attribute un variants se hata kar delete kar deta hai.
* **Product description:** ab rich-text editor hai (bold, italic, underline, headings, lists, quote, link). Save par HTML saaf (sanitise) kiya jata hai (`core/richtext.py`).
* **Add/Edit product:** Save wala floating card hata kar neeche ek hi Save bar lagi hai jo scroll par bhi neeche rehti hai.
* **Variants "Quick add" table:** naam comma se likhein (Small, Medium, Large) -> har naam ki ek variant row. Har row: unique Variant name, Cost, Selling price (khali = pehli variant jaisi), Opening qty (sirf nayi variant ki shuru ki stock), Barcode (khali ho to save par khud ban jata hai). Slider icon se SKU, image, Active aur attributes (Color=Red, Size=Large) khulte hain.

## Owner console UI (v3)

Gradient/glow background, glass topbar, rang-birangi KPI cards, dark theme, aur sidebar ka collapse button - collapse karne par sirf icons rehte hain aur hover par naam ka tooltip aata hai (choice browser mein yaad rehti hai).
