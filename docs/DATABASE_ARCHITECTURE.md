# Database architecture

SQLite for development; every model works on PostgreSQL / MySQL (no vendor-specific SQL; constraints are Django `CheckConstraint` /
`UniqueConstraint`). All migrations are additive. The ERP tables, by app:

| App | Tables (models) |
|---|---|
| company | Company (and `tenants.Store.company`, `Store.code`, `Warehouse.code`, `Warehouse.warehouse_type` added) |
| numbering | DocumentSequence |
| audit | AuditLog (immutable) |
| attachments | Attachment |
| rbac | ErpPermission (unmanaged; only carries the extra permissions) |
| approvals | ApprovalWorkflow, ApprovalLevel, ApprovalRequest, ApprovalAssignment, ApprovalAction |
| accounting | Account, CostCenter, AccountingPeriodLock, JournalEntry, JournalLine, BankAccount, BankStatementLine, FixedAsset, DepreciationEntry, Budget, BudgetLine |
| masters | PaymentTerm, TaxRate, ItemProfile, VendorProfile, CustomerProfile, ImportJob |
| stock | StockPolicy, Batch, BatchStock, ItemWarehouseCost, StockLedgerEntry (immutable), TransferOrder(+Item), AdjustmentOrder(+Item), StockCount(+Line) |
| quality | DefectType, QualityCheck, QualityParameter, QualityDefect |
| hr | Department, Designation, Shift, Holiday, Employee, Attendance, LeaveType, LeaveBalance, LeaveRequest, PerformanceReview, EmployeeLoan, LoanRepayment |
| procurement | PurchaseRequisition(+Item), RFQ(+Item, +Vendor), VendorQuotation(+Item), PurchaseOrderExt, GoodsReceipt(+Line), PurchaseInvoice(+Line), VendorPayment(+Allocation), VendorReturn(+Line) |
| production | Machine, Operation, BOM, BOMItem, BOMOperation, WorkOrder, WorkOrderMaterial, MaterialIssue(+Line), JobCard, ProductionReceipt |
| distribution | SalesQuotation(+Item), SalesOrder(+Item), DeliveryChallan(+Line), SalesInvoice(+Line), CustomerReceipt(+Allocation), ERPSalesReturn(+Line) |
| pos_erp | PosSettings, PosReturn, CashDrop, PosPostingIssue |
| payroll | SalaryStructure, PayrollRun, PayrollLine, PayrollLoanDeduction |
| notifications | Notification |

## Relationships to the legacy POS

```
Store (branch) --company--> Company
catalog.ProductVariant  1-1  masters.ItemProfile
suppliers.Supplier      1-1  masters.VendorProfile
customers.Customer      1-1  masters.CustomerProfile
suppliers.PurchaseOrder 1-1  procurement.PurchaseOrderExt
sales.SaleReturn        1-1  pos_erp.PosReturn          payments.Refund 1-1 pos_erp.PosReturn
inventory.StockItem  (still the quick quantity)  <->  stock.StockLedgerEntry (the audited history)
```

`StockItem.quantity` is kept in step by every ledger movement; `check_stock_ledger` compares the two and
`backfill_stock_ledger` creates opening ledger rows for stock that existed before the ledger.

## Integrity

* Immutable tables: AuditLog, StockLedgerEntry, posted JournalEntry/JournalLine (queryset `update`/`delete` raise).
* Money columns are `DecimalField` (14-16 digits, 2 decimals; quantities 3, unit costs 4).
* Quantities, amounts and positive-only fields have database `CheckConstraint`s.
* Unique constraints on business keys (employee code per company, vendor invoice number per vendor, one open register session ...).
* Documents never reference a row of another company: services check `company_of_store(...)` before linking.

## Backups

Back up `db.sqlite3` (or `pg_dump`) before every `migrate` on a real database. `_erp_backup/` holds the originals of the two files edited in Phase 1.
