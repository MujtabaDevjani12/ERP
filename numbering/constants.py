"""
Default numbering scheme for the ERP document types. Each entry:
    document_type -> (prefix, include_year, padding)

These are only DEFAULTS used the first time a sequence is needed; admins
can change prefix/padding/next_number per company or per branch in the
admin afterwards. This is separate from (and does not touch) the legacy
configuration.NumberingPrefix that the current POS uses.
"""

DEFAULTS = {
    "purchase_requisition": ("PR", True, 5),
    "rfq": ("RFQ", True, 5),
    "purchase_order": ("PO", True, 5),
    "goods_receipt": ("GRN", True, 5),
    "work_order": ("WO", True, 5),
    "job_card": ("JC", True, 5),
    "quality_check": ("QC", True, 5),
    "sales_order": ("SO", True, 5),
    "delivery_challan": ("DC", True, 5),
    "sales_invoice": ("INV", True, 5),
    "sales_return": ("RET", True, 5),
    "pos_sale": ("POS", True, 5),
    "journal_voucher": ("JV", True, 5),
    "payment_voucher": ("PV", True, 5),
    "receipt_voucher": ("RV", True, 5),
    "employee": ("EMP", False, 5),
    "payroll": ("PAY", True, 5),
    "vendor": ("VEN", False, 5),
    "customer": ("CUS", False, 5),
    "quotation": ("QTN", True, 5),
    "vendor_quotation": ("VQ", True, 5),
    "purchase_invoice": ("PINV", True, 5),
    "vendor_payment": ("VPY", True, 5),
    "purchase_return": ("PRT", True, 5),
    "production_receipt": ("PRR", True, 5),
    "customer_receipt": ("CRC", True, 5),
    "credit_note": ("CN", True, 5),
    "stock_transfer": ("TRF", True, 5),
    "stock_adjustment": ("ADJ", True, 5),
    "stock_count": ("CNT", True, 5),
    "material_issue": ("MI", True, 5),
    "leave_request": ("LV", True, 5),
    "employee_loan": ("LN", True, 5),
    "system_journal": ("SJ", True, 5),
    "contra_voucher": ("CV", True, 5),
    "debit_note": ("DN", True, 5),
    "credit_note_voucher": ("CNV", True, 5),
    "opening_voucher": ("OB", True, 5),
}

DOCUMENT_TYPE_CHOICES = sorted((key, key.replace("_", " ").title()) for key in DEFAULTS)
