# REST API (read-only)

Needs `pip install djangorestframework`. Without it the `/api/v1/` URLs simply do not exist and nothing else is affected.

* Base URL: `/api/v1/`
* Authentication: Django session (log in at `/accounts/login/`) or HTTP Basic (**only over HTTPS**). No API keys are stored in the project.
* Every endpoint is scoped to the caller's company and needs the Django *view* permission of the underlying model
  (superusers pass). Without it: `403`.
* The API cannot create, change or delete anything (`POST/PUT/DELETE` return `405`). Writes go through the admin / services so approval,
  audit and accounting rules always apply.
* Lists are paged: `?page=2&page_size=100` (max 200). Newest first.

| Endpoint | Content | Permission needed |
|---|---|---|
| `GET /me/` | who you are, company, branch | login |
| `GET /dashboard/` | KPI blocks your role may see | login |
| `GET /reports/` | reports you may run | login |
| `GET /reports/<key>/?from=YYYY-MM-DD&to=...&as_of=...` | `{title, headers, rows}` | the report's permission |
| `GET /accounts/`, `/journal-entries/` | chart of accounts, journals | accounting view |
| `GET /stock-levels/`, `/stock-ledger/` | weighted-average levels, movements | stock view |
| `GET /sales-orders/`, `/sales-invoices/` | wholesale documents | distribution view |
| `GET /purchase-orders/`, `/purchase-invoices/` | purchasing documents | procurement view |
| `GET /work-orders/` | production | production view |
| `GET /employees/` | HR master (CNIC, bank, address, emergency contact are **not** exposed) | hr view |

Report keys: `trial_balance`, `profit_and_loss`, `balance_sheet`, `cash_flow`, `tax`, `ar_aging`, `sales_by_customer`, `gross_margin`,
`pos_daily`, `ap_aging`, `purchase_by_vendor`, `stock_valuation`, `gl_vs_stock`, `reorder`, `expiring`, `work_orders`, `wip`, `scrap`, `machines`.

Example (PowerShell, development server):

```powershell
curl.exe -u admin:YOUR_PASSWORD "http://127.0.0.1:8000/api/v1/reports/trial_balance/?as_of=2026-09-30"
```

Rate limiting, token/JWT authentication and write endpoints are **not** implemented.
