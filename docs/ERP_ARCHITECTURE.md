# ERP Architecture

The ERP is built **on top of** the working Django POS. The original apps (`catalog`, `inventory`, `sales`, `register`, `suppliers`,
`customers`, `payments` ...) keep their tables and screens. New apps add a company layer, controls, accounting and the
manufacturing / wholesale / procurement processes.

## Layers

```
company -> tenants (Store = branch, Warehouse) -> legacy POS apps (unchanged)
   |
   +- numbering   document numbers  PO-2026-00001  (per company / branch / fiscal year)
   +- audit       immutable audit trail (who, when, IP, old -> new)
   +- attachments files on any document
   +- rbac        21 role groups + action permissions (approve / post / reverse / pay ...)
   +- approvals   configurable workflows (levels, amount thresholds, delegation)
   +- accounting  double-entry ledger, periods, bank, assets, budgets, reports
   +- masters     payment terms, tax rates, item/vendor/customer profiles, Excel import
   +- stock       stock ledger, weighted-average cost, batches, transfers, adjustments, counts
   +- quality     inspections (incoming / in-process / final), defects
   +- hr          employees, attendance, leave, loans, reviews
   +- procurement requisition -> RFQ -> quotation -> PO -> GRN -> invoice -> payment, vendor returns
   +- production  BOM, work orders, material issue, job cards, finished goods, costing
   +- distribution quotation -> order -> delivery -> invoice -> receipt, sales returns
   +- pos_erp     POS returns/refunds, cash drops, shift close, OPTIONAL accounting posting
   +- payroll     salary structure, payroll run, payslips, bank file
   +- notifications  in-app inbox + optional e-mail alerts
   +- reports_erp dashboard, report pages, Excel / PDF export
   +- api         read-only REST API (optional, needs djangorestframework)
```

## Design rules (followed everywhere)

* **Additive only.** New tables, new apps, one-to-one "profile" tables hanging off legacy rows (`ItemProfile` -> `ProductVariant`,
  `VendorProfile` -> `Supplier`, `CustomerProfile` -> `Customer`, `PurchaseOrderExt` -> `suppliers.PurchaseOrder`).
  No legacy column is dropped or renamed.
* **Services own the business logic.** Every step is a function in `<app>/services.py`, decorated with `@transaction.atomic`,
  using `select_for_update` on rows it changes. Admin actions, views, the API and tests all call the same functions.
* **Everything that matters leaves a trail.** `log_action()` writes an immutable `AuditLog` row; journals, stock ledger rows and
  approval actions are immutable too.
* **Permissions are explicit.** `rbac.require(user, "approve_document")` is called inside the service, not only in the UI.
  `user=None` means "the system" (used by approval handlers and tests).
* **Approval = status binding.** A workflow is optional. With no workflow for a document type the document is auto-approved at once;
  with a workflow it stays in `pending_approval` until the last level approves, then the registered handler posts it.
* **Fault isolation.** `config/erp_apps.py` decides which ERP apps are installed; an app is skipped when something it depends on is missing.
  The POS hooks catch their own errors, so a posting problem never blocks a sale.
* **Money.** Decimals everywhere (`core.money.q2/q3/q4`), never floats. Totals use tax-exclusive rates.

## Cross-module data flow

| Event | Stock | Accounting |
|---|---|---|
| Goods receipt | + stock at PO cost | Dr Inventory, Cr GRNI |
| Purchase invoice | - | Dr GRNI (+ price variance), Dr Input tax, Cr Payables |
| Material issue | raw -> WIP | Dr WIP, Cr Raw inventory |
| Finished goods receipt | + FG at WO cost / quantity | Dr FG inventory, Cr WIP |
| Delivery (wholesale) | - stock at average cost | Dr COGS, Cr FG inventory |
| Sales invoice | - | Dr Receivables, Cr Sales, Cr Output tax |
| POS sale (if enabled) | - stock | Dr Receivables, Cr Retail sales, Cr Output tax; Dr COGS, Cr Inventory |
| Payroll approval | - | Dr Salary expense, Cr Salary payable / tax payable / loans |

## Where the screens are

Most ERP documents are managed in the **Django admin** (list, filter, search, and *actions* that call the services:
Submit, Approve, Post, Receive ...). `/erp/` is the dashboard with role-aware KPIs and the report pages; `/notifications/` is the inbox;
`/api/v1/` is the REST API. The original POS screens are unchanged.
