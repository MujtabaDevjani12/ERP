# Accounting rules

## Core rules enforced by `accounting.services.create_journal_entry`

1. **Debits equal credits** (to the paisa) or the entry is rejected (`UnbalancedJournal`). At least two non-zero lines.
2. A line has a debit **or** a credit, never both, never negative.
3. Lines post to **detail accounts** only (not group accounts) and only active accounts of the same company.
4. **Period lock.** Nothing can be posted on or before `AccountingPeriodLock.locked_through`.
5. **Posted entries are immutable.** Mistakes are fixed with `reverse_entry` (a new, opposite entry that references the original).
   Approved accounting transactions are never hard-deleted.
6. **Idempotent by source.** Each business document posts with `source=(type, id)`; calling the posting twice returns the first entry.
7. Entry numbers come from the numbering service (`JV-2026-00001` ...), by voucher type.

## System accounts

Code never hard-codes an account number; it asks for a **system key** (`cash`, `bank`, `ar`, `ap`, `inventory`, `raw_inventory`,
`wip_inventory`, `fg_inventory`, `grni`, `ppv`, `cogs`, `input_tax`, `output_tax`, `sales_retail`, `sales_wholesale`, `sales_returns`,
`salary_expense`, `salary_payable`, `payroll_tax_payable`, `employee_loans`, `production_variance`, `cash_over_short`,
`gift_card_liability`, `opening_balance_equity` ...). `seed_chart_of_accounts` creates a default chart; you can rename or re-code
accounts freely as long as the system keys stay attached.

## Posting map

| Document | Debit | Credit |
|---|---|---|
| Goods receipt | Inventory (by warehouse type) | GRNI (goods received not invoiced) |
| Purchase invoice | GRNI; Price variance (if billed > received cost, else credit); Input tax | Accounts payable |
| Vendor payment | Accounts payable | Cash / Bank |
| Vendor return | Accounts payable (+ input tax reversal) | Inventory |
| Material issue to production | WIP | Raw inventory |
| Labour / overhead on job card | WIP | Production overhead absorbed |
| Finished goods receipt | FG inventory | WIP |
| Work order close | Production variance (if WIP left) | WIP |
| Delivery challan | COGS | FG inventory |
| Sales invoice | Accounts receivable | Sales (wholesale), Output tax |
| Customer receipt | Cash / Bank | Accounts receivable |
| Sales return (ERP) | Sales returns, Output tax; Inventory (if restocked) | Accounts receivable; COGS |
| Stock adjustment (loss) | Stock adjustment expense | Inventory (gain is the reverse) |
| Stock transfer | In-transit while dispatched, then destination inventory | Source inventory |
| Payroll posting | Salary expense | Salary payable, Payroll tax payable, Employee loans (recoveries) |
| Salary payment | Salary payable | Cash / Bank |
| **POS sale** (only when enabled) | Accounts receivable | Retail sales, Output tax |
| POS cost of sale | COGS | Inventory |
| POS payment | Cash / Bank / Gift-card liability | Accounts receivable |
| POS return | Sales returns, Output tax | Accounts receivable |
| POS cash refund | Accounts receivable | Cash |
| Shift close over/short | Cash (over) or Cash over/short (short) | Cash over/short (over) or Cash (short) |

## Inventory valuation

Weighted-average cost per item per warehouse. Inbound movements carry a cost; outbound movements are valued at the current average.
`inventory_gl_vs_stock` compares the GL inventory accounts with the stock valuation: any difference means something posted to one
side only. FIFO / standard cost are not implemented.

## Reports

Trial balance, general ledger, profit & loss, balance sheet, cash flow (direct method), tax summary, AR / AP ageing. All are plain
functions in `accounting/reports.py` and are exposed through `/erp/report/<key>/` (screen, Excel, PDF) and `/api/v1/reports/<key>/`.

## POS accounting switch

`POS accounting` is **off** by default (`pos_erp.PosSettings.post_to_accounting`). Turn it on per company in the admin. To bring
older sales into the books: `python manage.py post_pos_to_accounting --company "Name" --apply` (dry-run without `--apply`).
Failed postings are listed in *POS posting issues*; fix the cause (for example an inactive account) and use the **Retry** action.
