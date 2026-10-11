"""
The 21 ERP roles as Django Groups + the permissions each one gets.
Built by `manage.py seed_roles` (safe to re-run: it only ADDS the listed
permissions to each group, it never removes ones you added by hand).

Grant tuple: (app_label, actions, optional list of lowercase model names).
Actions are Django's view/add/change/delete plus the custom ERP actions
from rbac.ErpPermission (approve/reject/cancel/post/reverse/print/export/
import/pay -> pass them as "erp:approve" etc.).
"""
V, A, C, X = "view", "add", "change", "delete"
VIEW = (V,)
CRUD_NO_DELETE = (V, A, C)
FULL = (V, A, C, X)

APPROVE = "erp:approve_document"
REJECT = "erp:reject_document"
CANCEL = "erp:cancel_document"
POST = "erp:post_document"
REVERSE = "erp:reverse_document"
PRINT = "erp:print_document"
EXPORT = "erp:export_document"
IMPORT = "erp:import_document"
PAY = "erp:pay_document"

ERP_APPS = [
    "company", "numbering", "audit", "attachments", "approvals", "accounting", "masters", "stock",
    "quality", "hr", "procurement", "production", "distribution", "pos_erp", "payroll", "notifications",
]
LEGACY_POS_APPS = [
    "catalog", "inventory", "suppliers", "customers", "register", "sales", "payments", "expenses",
    "pricing", "documents", "devices",
]

# Reusable fragments
_VIEW_ALL = [(app, VIEW) for app in ERP_APPS + LEGACY_POS_APPS]
_DOC_ACTIONS = (APPROVE, REJECT, PRINT, EXPORT)

ROLES = {
    "Super Admin": "ALL",
    "CEO": _VIEW_ALL + [("rbac", (APPROVE, REJECT, PRINT, EXPORT))],
    "General Manager": _VIEW_ALL + [("rbac", (APPROVE, REJECT, CANCEL, PRINT, EXPORT))],

    "Purchase Manager": [
        ("procurement", FULL), ("masters", CRUD_NO_DELETE), ("suppliers", CRUD_NO_DELETE),
        ("stock", VIEW), ("quality", VIEW), ("accounting", VIEW),
        ("rbac", (APPROVE, REJECT, CANCEL, PRINT, EXPORT, IMPORT)),
    ],
    "Purchase Officer": [
        ("procurement", CRUD_NO_DELETE, ["purchaserequisition", "purchaserequisitionitem", "rfq", "rfqitem",
                                         "rfqvendor", "vendorquotation", "vendorquotationitem"]),
        ("procurement", VIEW, ["goodsreceipt", "goodsreceiptline", "purchaseinvoice", "purchaseinvoiceline"]),
        ("suppliers", VIEW), ("masters", VIEW), ("stock", VIEW),
        ("rbac", (PRINT,)),
    ],
    "Warehouse Manager": [
        ("stock", FULL), ("procurement", CRUD_NO_DELETE, ["goodsreceipt", "goodsreceiptline"]),
        ("masters", VIEW), ("quality", VIEW), ("inventory", FULL),
        ("rbac", (APPROVE, REJECT, POST, PRINT, EXPORT, IMPORT)),
    ],
    "Warehouse Staff": [
        ("stock", CRUD_NO_DELETE), ("procurement", (V, A), ["goodsreceipt", "goodsreceiptline"]),
        ("masters", VIEW), ("inventory", VIEW), ("rbac", (PRINT,)),
    ],

    "Production Manager": [
        ("production", FULL), ("stock", VIEW), ("quality", VIEW), ("masters", VIEW), ("hr", VIEW),
        ("rbac", (APPROVE, REJECT, CANCEL, POST, PRINT, EXPORT)),
    ],
    "Production Supervisor": [
        ("production", CRUD_NO_DELETE), ("stock", VIEW), ("quality", VIEW), ("masters", VIEW),
        ("rbac", (PRINT,)),
    ],

    "QC Manager": [
        ("quality", FULL), ("procurement", VIEW), ("production", VIEW), ("stock", VIEW),
        ("rbac", (APPROVE, REJECT, PRINT, EXPORT)),
    ],
    "QC Inspector": [
        ("quality", CRUD_NO_DELETE), ("procurement", VIEW, ["goodsreceipt", "goodsreceiptline"]),
        ("production", VIEW), ("rbac", (PRINT,)),
    ],

    "Sales Manager": [
        ("distribution", FULL), ("masters", CRUD_NO_DELETE), ("customers", CRUD_NO_DELETE), ("stock", VIEW),
        ("accounting", VIEW), ("rbac", (APPROVE, REJECT, CANCEL, PRINT, EXPORT, IMPORT)),
    ],
    "Sales Officer": [
        ("distribution", CRUD_NO_DELETE), ("customers", CRUD_NO_DELETE), ("masters", VIEW), ("stock", VIEW),
        ("rbac", (PRINT,)),
    ],

    "POS Manager": [
        ("sales", FULL), ("register", FULL), ("payments", FULL), ("expenses", CRUD_NO_DELETE),
        ("catalog", CRUD_NO_DELETE), ("customers", CRUD_NO_DELETE), ("inventory", CRUD_NO_DELETE),
        ("pos_erp", FULL), ("pricing", VIEW), ("stock", VIEW),
        ("rbac", (APPROVE, REJECT, POST, PRINT, EXPORT, PAY)),
    ],
    "POS Cashier": [
        ("sales", (V, A)), ("register", (V, A, C)), ("payments", (V, A)), ("catalog", VIEW),
        ("customers", (V, A)), ("inventory", VIEW), ("pos_erp", (V, A), ["cashdrop"]), ("rbac", (PRINT,)),
    ],
    "Outlet Manager": [
        ("sales", CRUD_NO_DELETE), ("register", FULL), ("payments", CRUD_NO_DELETE), ("expenses", CRUD_NO_DELETE),
        ("catalog", VIEW), ("customers", CRUD_NO_DELETE), ("inventory", CRUD_NO_DELETE), ("stock", CRUD_NO_DELETE),
        ("pos_erp", CRUD_NO_DELETE), ("rbac", (APPROVE, PRINT, EXPORT)),
    ],

    "Accountant": [
        ("accounting", CRUD_NO_DELETE), ("procurement", VIEW), ("distribution", VIEW), ("payroll", VIEW),
        ("pos_erp", VIEW), ("masters", VIEW), ("rbac", (POST, PRINT, EXPORT, IMPORT, PAY)),
    ],
    "Finance Manager": [
        ("accounting", FULL), ("procurement", VIEW), ("distribution", VIEW), ("payroll", VIEW), ("pos_erp", VIEW),
        ("masters", CRUD_NO_DELETE), ("rbac", (APPROVE, REJECT, CANCEL, POST, REVERSE, PRINT, EXPORT, IMPORT, PAY)),
    ],

    "HR Manager": [
        ("hr", FULL), ("payroll", VIEW), ("attachments", CRUD_NO_DELETE),
        ("rbac", (APPROVE, REJECT, PRINT, EXPORT, IMPORT)),
    ],
    "HR Officer": [
        ("hr", CRUD_NO_DELETE), ("attachments", CRUD_NO_DELETE), ("rbac", (PRINT, IMPORT)),
    ],
    "Payroll Officer": [
        ("payroll", CRUD_NO_DELETE), ("hr", VIEW), ("accounting", VIEW),
        ("rbac", (PRINT, EXPORT)),
    ],
}
