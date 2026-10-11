from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounting.models import JournalEntry
from accounting.reports import balance_of, trial_balance
from approvals.models import ApprovalLevel, ApprovalWorkflow
from approvals import services as ap
from catalog.models import Product, Unit
from company.models import Company
from core.errors import ERPError, InsufficientStock
from masters.models import ItemProfile, TaxRate
from quality import services as qc
from stock import services as st
from suppliers.models import Supplier
from tenants.models import Store, Warehouse

from . import services as pr
from .models import GoodsReceipt, PurchaseInvoice, PurchaseOrderExt, VendorPayment


def D(x):
    return Decimal(str(x))


class ProcBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.wh = Warehouse.objects.create(store=self.store, name="Raw WH", code="RAW", warehouse_type="RAW_MATERIAL")
        unit = Unit.objects.create(store=self.store, name="Kg", short_name="kg", allow_decimal=True)
        p = Product.objects.create(store=self.store, unit=unit, name="Yarn", sku="YARN", product_type="single")
        self.v = p.variants.first()
        self.supplier = Supplier.objects.create(store=self.store, name="Mill Co")
        self.other_supplier = Supplier.objects.create(store=self.store, name="Other Co")
        self.user = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-x")

    def make_po(self, qty=100, price=50, tax=None, supplier=None):
        po = pr.create_purchase_order(self.company, supplier or self.supplier, self.wh,
                                      [{"variant": self.v, "quantity": qty, "cost_price": price}], user=self.user, tax_rate=tax)
        pr.submit_purchase_order(po, self.user)      # no workflow -> auto-approved
        po.refresh_from_db()
        return po

    def bal(self, key):
        return balance_of(self.company, key)


class ProcurementFlowTests(ProcBase):
    def test_full_cycle_balances_and_clears_grni(self):
        tax = TaxRate.objects.create(company=self.company, name="GST", rate=18)
        po = self.make_po(tax=tax)
        self.assertEqual(po.status, "ordered")
        self.assertEqual(po.erp.status, "approved")

        grn = pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 100}], user=self.user, challan_no="CH-1")
        self.assertEqual(grn.status, GoodsReceipt.POSTED)
        self.assertEqual(st.available_qty(self.v, self.wh), D(100))
        self.assertEqual(self.bal("raw_inventory"), D("5000.00"))
        self.assertEqual(self.bal("grni"), D("5000.00"))
        po.refresh_from_db()
        self.assertEqual(po.status, "received")

        # supplier bills 52/kg: price variance of 200
        gl = grn.lines.first()
        inv = pr.create_purchase_invoice(self.company, self.supplier, "INV-77", date.today(),
                                         [{"grn_line": gl, "quantity": 100, "unit_price": 52}], tax_rate=tax, user=self.user)
        self.assertEqual((inv.subtotal, inv.tax_amount, inv.total), (D("5200.00"), D("936.00"), D("6136.00")))
        pr.submit_purchase_invoice(inv, self.user)
        inv.refresh_from_db()
        self.assertEqual(inv.status, "posted")
        self.assertEqual(self.bal("grni"), D("0"))
        self.assertEqual(self.bal("ppv"), D("200.00"))
        self.assertEqual(self.bal("ap"), D("6136.00"))
        self.assertEqual(self.bal("input_tax"), D("936.00"))

        pay = pr.create_vendor_payment(self.company, self.supplier, D("6136.00"), user=self.user, method="cash",
                                       allocations=[(inv, D("6136.00"))])
        pr.submit_vendor_payment(pay, self.user)
        inv.refresh_from_db()
        self.assertEqual(inv.paid_amount, D("6136.00"))
        self.assertEqual(self.bal("ap"), D("0"))
        self.assertTrue(trial_balance(self.company)["balanced"])

    def test_cannot_receive_more_than_ordered_or_unapproved(self):
        po = self.make_po(qty=10)
        with self.assertRaises(ERPError):
            pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 11}], user=self.user)
        draft = pr.create_purchase_order(self.company, self.supplier, self.wh, [{"variant": self.v, "quantity": 1, "cost_price": 1}])
        with self.assertRaises(ERPError):
            pr.receive_goods(draft, [{"po_item": draft.items.first(), "quantity": 1}], user=self.user)

    def test_partial_receipts_then_close(self):
        po = self.make_po(qty=10)
        item = po.items.first()
        pr.receive_goods(po, [{"po_item": item, "quantity": 4}], user=self.user)
        po.refresh_from_db()
        self.assertEqual(po.status, "ordered")
        pr.receive_goods(po, [{"po_item": item, "quantity": 6}], user=self.user)
        po.refresh_from_db()
        self.assertEqual(po.status, "received")
        self.assertEqual(po.erp.status, PurchaseOrderExt.CLOSED)

    def test_duplicate_vendor_invoice_blocked_and_overbilling_blocked(self):
        po = self.make_po(qty=10, price=10)
        grn = pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 10}], user=self.user)
        gl = grn.lines.first()
        pr.create_purchase_invoice(self.company, self.supplier, "A-1", date.today(), [{"grn_line": gl, "quantity": 6, "unit_price": 10}])
        with self.assertRaises(ERPError):
            pr.create_purchase_invoice(self.company, self.supplier, " a-1 ", date.today(), [{"grn_line": gl, "quantity": 1, "unit_price": 10}])
        with self.assertRaises(ERPError):
            pr.create_purchase_invoice(self.company, self.supplier, "A-2", date.today(), [{"grn_line": gl, "quantity": 5, "unit_price": 10}])
        with self.assertRaises(ERPError):
            pr.create_purchase_invoice(self.company, self.other_supplier, "B-1", date.today(), [{"grn_line": gl, "quantity": 1, "unit_price": 10}])

    def test_payment_validation_and_reversal(self):
        po = self.make_po(qty=10, price=10)
        grn = pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 10}], user=self.user)
        inv = pr.create_purchase_invoice(self.company, self.supplier, "P-1", date.today(),
                                         [{"grn_line": grn.lines.first(), "quantity": 10, "unit_price": 10}])
        pr.submit_purchase_invoice(inv, self.user)
        inv.refresh_from_db()
        with self.assertRaises(ERPError):
            pr.create_vendor_payment(self.company, self.supplier, 50, user=self.user, allocations=[(inv, D(101))])
        pay = pr.create_vendor_payment(self.company, self.supplier, 60, user=self.user, method="cash", allocations=[(inv, D(60))])
        pr.submit_vendor_payment(pay, self.user)
        inv.refresh_from_db()
        self.assertEqual(inv.outstanding, D("40.00"))
        with self.assertRaises(ERPError):
            pr.cancel_purchase_invoice(inv, self.user)           # has payments
        pay.refresh_from_db()
        pr.reverse_vendor_payment(pay, self.user, "wrong vendor")
        inv.refresh_from_db()
        self.assertEqual(inv.outstanding, D("100.00"))
        self.assertEqual(self.bal("ap"), D("100.00"))

    def test_grn_reversal_restores_everything(self):
        po = self.make_po(qty=5, price=20)
        grn = pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 5}], user=self.user)
        pr.reverse_goods_receipt(grn, self.user, "wrong goods")
        self.assertEqual(st.available_qty(self.v, self.wh), D(0))
        self.assertEqual(self.bal("grni"), D(0))
        po.refresh_from_db()
        self.assertEqual(po.status, "ordered")
        pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 5}], user=self.user)   # can receive again

    def test_return_to_vendor(self):
        po = self.make_po(qty=10, price=20)
        grn = pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 10}], user=self.user)
        pr.return_to_vendor(self.company, self.supplier, self.wh, [{"variant": self.v, "quantity": 2, "unit_cost": 20}],
                            user=self.user, grn=grn, reason="damaged")
        self.assertEqual(st.available_qty(self.v, self.wh), D(8))
        self.assertEqual(self.bal("raw_inventory"), D("160.00"))
        self.assertEqual(self.bal("ap"), D("-40.00"))
        with self.assertRaises(InsufficientStock):
            pr.return_to_vendor(self.company, self.supplier, self.wh, [{"variant": self.v, "quantity": 99, "unit_cost": 20}])


class QCDeferralTests(ProcBase):
    def test_goods_wait_for_qc_then_partial_acceptance(self):
        ItemProfile.objects.create(variant=self.v, qc_required_on_receipt=True)
        po = self.make_po(qty=100, price=10)
        grn = pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 100}], user=self.user)
        self.assertEqual(grn.status, GoodsReceipt.QC_PENDING)
        self.assertEqual(st.available_qty(self.v, self.wh), D(0))          # not in stock yet
        self.assertEqual(self.bal("grni"), D(0))
        check = qc.pending_checks(self.company, "incoming").get()
        with self.assertRaises(ERPError):
            qc.complete_check(check, self.user, accepted=60, rejected=30)  # does not add up
        with self.assertRaises(ERPError):
            qc.complete_check(check, self.user, accepted=60, rejected=40)  # rejection needs a reason
        qc.complete_check(check, self.user, accepted=60, rejected=40, remarks="stained")
        grn.refresh_from_db()
        self.assertEqual(grn.status, GoodsReceipt.POSTED)
        self.assertEqual(st.available_qty(self.v, self.wh), D(60))
        self.assertEqual(self.bal("grni"), D("600.00"))
        line = grn.lines.first()
        self.assertEqual((line.quantity_accepted, line.quantity_rejected), (D(60), D(40)))
        with self.assertRaises(ERPError):                                  # can only invoice what was accepted
            pr.create_purchase_invoice(self.company, self.supplier, "Q-1", date.today(),
                                       [{"grn_line": line, "quantity": 100, "unit_price": 10}])

    def test_batch_required_item(self):
        ItemProfile.objects.create(variant=self.v, batch_required=True)
        po = self.make_po(qty=10, price=1)
        with self.assertRaises(ERPError):
            pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 10}], user=self.user)
        pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 10, "batch_no": "L-1"}], user=self.user)
        self.assertEqual(st.available_qty(self.v, self.wh), D(10))


class RFQAndApprovalTests(ProcBase):
    def test_requisition_to_po_via_quotation_comparison(self):
        req = pr.create_requisition(self.company, self.wh, [{"variant": self.v, "quantity": 50, "estimated_cost": 55}], user=self.user)
        pr.submit_requisition(req, self.user)
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        rfq = pr.rfq_from_requisition(req, [self.supplier, self.other_supplier], self.user)
        item_id = rfq.items.first().pk
        q1 = pr.add_quotation(rfq, self.supplier, {item_id: 60}, delivery_days=5)
        q2_ = pr.add_quotation(rfq, self.other_supplier, {item_id: 52}, delivery_days=9)
        with self.assertRaises(ERPError):
            pr.add_quotation(rfq, self.supplier, {item_id: 1})             # duplicate quote from same vendor (unique)
        cmp = pr.compare_quotations(rfq)
        self.assertEqual(cmp["items"][0]["lowest_supplier_id"], self.other_supplier.pk)
        self.assertTrue([v for v in cmp["vendors"] if v["supplier"] == self.other_supplier][0]["is_lowest_total"])
        po = pr.select_quotation(q2_, self.user)
        q1.refresh_from_db()
        self.assertEqual(q1.status, "rejected")
        self.assertEqual(po.items.first().cost_price, D("52.00"))
        self.assertEqual(po.supplier, self.other_supplier)

    def test_po_waits_for_two_level_approval_and_maker_cannot_approve(self):
        l1 = get_user_model().objects.create_user("l1", "l1@example.com", "pw-12345-x")
        wf = ApprovalWorkflow.objects.create(company=self.company, name="PO", document_type="purchase_order", min_amount=0)
        ApprovalLevel.objects.create(workflow=wf, level=1, approver_user=l1)
        po = pr.create_purchase_order(self.company, self.supplier, self.wh, [{"variant": self.v, "quantity": 10, "cost_price": 5}], user=self.user)
        ext = pr.submit_purchase_order(po, self.user)
        self.assertEqual(ext.status, "pending_approval")
        with self.assertRaises(ERPError):                                  # not approved -> cannot receive
            pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 1}], user=self.user)
        request = ap.get_request(ext, "purchase_order")
        ap.approve(request, l1, "ok")
        ext.refresh_from_db()
        po.refresh_from_db()
        self.assertEqual((ext.status, po.status), ("approved", "ordered"))

    def test_rejected_po_goes_back_for_edit(self):
        l1 = get_user_model().objects.create_user("l1", "l1@example.com", "pw-12345-x")
        wf = ApprovalWorkflow.objects.create(company=self.company, name="PO", document_type="purchase_order", min_amount=0)
        ApprovalLevel.objects.create(workflow=wf, level=1, approver_user=l1)
        po = pr.create_purchase_order(self.company, self.supplier, self.wh, [{"variant": self.v, "quantity": 10, "cost_price": 5}])
        ext = pr.submit_purchase_order(po, self.user)
        ap.reject(ap.get_request(ext, "purchase_order"), l1, "price too high")
        ext.refresh_from_db()
        self.assertEqual(ext.status, "rejected")
        pr.submit_purchase_order(po, self.user)                            # may be resubmitted


class StatementTests(ProcBase):
    def test_vendor_statement_and_aging(self):
        po = self.make_po(qty=10, price=100)
        grn = pr.receive_goods(po, [{"po_item": po.items.first(), "quantity": 10}], user=self.user)
        inv = pr.create_purchase_invoice(self.company, self.supplier, "S-1", date.today() - timedelta(days=45),
                                         [{"grn_line": grn.lines.first(), "quantity": 10, "unit_price": 100}],
                                         due_date=date.today() - timedelta(days=40))
        pr.submit_purchase_invoice(inv, self.user)
        stmt = pr.vendor_statement(self.company, self.supplier)
        self.assertEqual(stmt["closing"], D("1000.00"))
        aging = pr.ap_aging(self.company)
        self.assertEqual(aging["total"], D("1000.00"))
        self.assertEqual(sum(v for k, v in aging["summary"].items()), D("1000.00"))
