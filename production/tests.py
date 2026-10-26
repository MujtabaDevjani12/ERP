from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounting.reports import balance_of, trial_balance
from accounting.services import create_journal_entry
from catalog.models import Product, Unit
from company.models import Company
from core.errors import ERPError, InsufficientStock
from hr import services as hr
from masters.models import ItemProfile
from quality import services as qc
from stock import services as st
from tenants.models import Store, Warehouse

from . import reports
from . import services as pr
from .models import BOM, JobCard, Operation, WorkOrder


def D(x):
    return Decimal(str(x))


class ProdBase(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.raw = Warehouse.objects.create(store=self.store, name="Raw", code="RAW", warehouse_type="RAW_MATERIAL")
        self.fg = Warehouse.objects.create(store=self.store, name="FG", code="FG", warehouse_type="FINISHED_GOODS")
        unit = Unit.objects.create(store=self.store, name="Piece", short_name="pc", allow_decimal=True)

        def item(sku):
            return Product.objects.create(store=self.store, unit=unit, name=sku, sku=sku, product_type="single").variants.first()

        self.fabric, self.button, self.shirt = item("FAB"), item("BTN"), item("SHIRT")
        self.user = get_user_model().objects.create_superuser("boss", "b@example.com", "pw-12345-x")
        st.post_stock_movement(self.fabric, self.raw, 25, reference_type="OPENING", unit_cost=100, user=self.user)
        st.post_stock_movement(self.button, self.raw, 60, reference_type="OPENING", unit_cost=2, user=self.user)
        create_journal_entry(self.company, voucher_type="OPENING", narration="opening raw stock", lines=[
            {"account": "raw_inventory", "debit": 2620}, {"account": "opening_balance_equity", "credit": 2620}])
        self.cutting = Operation.objects.create(company=self.company, name="Cutting")
        self.stitching = Operation.objects.create(company=self.company, name="Stitching", qc_required=True)
        self.bom = pr.create_bom(
            self.company, self.shirt,
            [{"component": self.fabric, "quantity": 2}, {"component": self.button, "quantity": 5}],
            operations=[{"operation": self.cutting, "sequence": 10, "minutes_per_unit": 30, "labor_rate_per_hour": 60},
                        {"operation": self.stitching, "sequence": 20, "minutes_per_unit": 60, "labor_rate_per_hour": 30, "overhead_rate_per_hour": 20}],
            user=self.user)
        pr.activate_bom(self.bom, self.user)

    def bal(self, key):
        return balance_of(self.company, key)

    def released_wo(self, qty=10):
        wo = pr.create_work_order(self.bom, qty, self.raw, self.fg, user=self.user)
        return pr.submit_work_order(wo, self.user)        # no workflow -> released


class BOMTests(ProdBase):
    def test_versions_and_single_active(self):
        v2 = pr.new_bom_version(self.bom, self.user)
        self.assertEqual((v2.version, v2.status), (2, "draft"))
        pr.activate_bom(v2, self.user)
        self.bom.refresh_from_db()
        self.assertEqual(self.bom.status, "archived")
        self.assertEqual(BOM.objects.filter(variant=self.shirt, status="active").count(), 1)

    def test_cycle_and_self_reference_rejected(self):
        with self.assertRaises(ERPError):
            pr.create_bom(self.company, self.shirt, [{"component": self.shirt, "quantity": 1}])
        with self.assertRaises(ERPError):                 # fabric made from shirt, shirt made from fabric
            pr.create_bom(self.company, self.fabric, [{"component": self.shirt, "quantity": 1}])

    def test_work_order_checks_the_boms_current_status_not_a_stale_copy(self):
        stale = BOM.objects.get(pk=self.bom.pk)
        BOM.objects.filter(pk=self.bom.pk).update(status="archived")          # someone retired it after this copy was loaded
        with self.assertRaises(ERPError):
            pr.create_work_order(stale, 1, self.raw, self.fg, user=self.user)

    def test_work_order_needs_active_bom_and_cost_estimate(self):
        draft = pr.new_bom_version(self.bom, self.user)
        with self.assertRaises(ERPError):
            pr.create_work_order(draft, 5, self.raw, self.fg)
        cost = pr.bom_cost(self.bom, self.raw)
        self.assertEqual(cost["material"], D("210.00"))     # 2*100 + 5*2
        self.assertEqual(cost["labor"], D("60.00"))         # 0.5h*60 + 1h*30
        self.assertEqual(cost["overhead"], D("20.00"))
        self.assertEqual(cost["total"], D("290.00"))


class FullProductionTests(ProdBase):
    def run_to_stitching_done(self):
        wo = self.released_wo(10)
        self.assertEqual(wo.status, "released")
        self.assertEqual(wo.job_cards.count(), 2)
        pr.issue_materials(wo, user=self.user)
        wo.refresh_from_db()
        self.assertEqual(wo.status, "in_progress")
        cut, stitch = wo.job_cards.order_by("sequence")
        pr.start_job_card(cut, user=self.user)
        pr.complete_job_card(cut, 10, 0, user=self.user)
        pr.start_job_card(stitch, user=self.user)
        pr.complete_job_card(stitch, 9, 1, user=self.user)
        return wo

    def test_full_flow_costs_and_gl(self):
        wo = self.run_to_stitching_done()
        self.assertEqual(self.bal("wip_inventory"), D("2900.00"))
        stitch = wo.job_cards.get(sequence=20)
        self.assertEqual(stitch.status, JobCard.QC_PENDING)
        check = qc.pending_checks(self.company, "in_process").get()
        qc.complete_check(check, self.user, accepted=8, rejected=1, remarks="seam open")
        stitch.refresh_from_db()
        self.assertEqual((stitch.status, stitch.completed_quantity, stitch.rejected_quantity), ("completed", D(8), D(2)))

        with self.assertRaises(ERPError):
            pr.receive_finished_goods(wo, 9, user=self.user)          # only 8 passed the last step
        receipt = pr.receive_finished_goods(wo, 8, user=self.user)
        self.assertEqual(receipt.value, D("2320.00"))                 # 8 x (2900 / 10)
        self.assertEqual(st.available_qty(self.shirt, self.fg), D(8))
        self.assertEqual(st.avg_cost(self.shirt, self.fg), D("290.0000"))
        pr.complete_work_order(wo, self.user)
        pr.close_work_order(wo, self.user)
        wo.refresh_from_db()
        self.assertEqual(wo.status, "closed")
        self.assertEqual(wo.variance_written_off, D("580.00"))
        self.assertEqual(self.bal("wip_inventory"), D(0))
        self.assertEqual(self.bal("fg_inventory"), D("2320.00"))
        self.assertEqual(self.bal("production_variance"), D("580.00"))
        self.assertEqual(self.bal("raw_inventory"), D("520.00"))        # 2620 - 2100 issued
        self.assertTrue(trial_balance(self.company)["balanced"])
        cost = reports.work_order_cost(wo)
        self.assertEqual((cost["total"], cost["unit_cost"]), (D("2900.00"), D("290.00")))
        self.assertEqual(reports.production_efficiency(self.company)["received"], D(8))

    def test_operations_run_in_sequence_and_quantities_flow(self):
        wo = self.released_wo(10)
        pr.issue_materials(wo, user=self.user)
        cut, stitch = wo.job_cards.order_by("sequence")
        with self.assertRaises(ERPError):
            pr.start_job_card(stitch, user=self.user)                 # cutting not done
        pr.start_job_card(cut, user=self.user)
        with self.assertRaises(ERPError):
            pr.complete_job_card(cut, 11, 0, user=self.user)          # more than planned
        pr.complete_job_card(cut, 7, 0, user=self.user)
        pr.start_job_card(stitch, user=self.user)
        with self.assertRaises(ERPError):
            pr.complete_job_card(stitch, 8, 0, user=self.user)        # only 7 arrived

    def test_material_rules(self):
        wo = self.released_wo(10)
        m = wo.materials.get(component=self.fabric)
        with self.assertRaises(ERPError):
            pr.issue_materials(wo, {m.pk: 21}, user=self.user)        # needs 20
        pr.issue_materials(wo, {m.pk: 12}, user=self.user)
        with self.assertRaises(ERPError):
            pr.issue_materials(wo, {m.pk: 9}, user=self.user)         # 12 + 9 > 20
        pr.return_materials(wo, {m.pk: 2}, user=self.user)
        m.refresh_from_db()
        self.assertEqual((m.issued_quantity, m.issued_value), (D(10), D("1000.00")))
        self.assertEqual(st.available_qty(self.fabric, self.raw), D(15))
        self.assertEqual(self.bal("wip_inventory"), D("1000.00"))

    def test_shortage_report_and_insufficient_stock(self):
        wo = self.released_wo(20)                                       # needs 40 fabric, only 25 in store
        rows = {r["material"].component.product.sku: r for r in pr.material_shortage(wo)}          # variant SKUs carry a suffix; the product SKU is "FAB"
        self.assertEqual(rows["FAB"]["shortage"], D(15))
        with self.assertRaises(InsufficientStock):
            pr.issue_materials(wo, user=self.user)
        self.assertEqual(st.available_qty(self.fabric, self.raw), D(25))   # all-or-nothing

    def test_cancel_rules_and_fg_needs_cost(self):
        wo = self.released_wo(10)
        with self.assertRaises(ERPError):
            pr.receive_finished_goods(wo, 1, user=self.user)            # not started / no cost
        pr.issue_materials(wo, user=self.user)
        with self.assertRaises(ERPError):
            pr.cancel_work_order(wo, self.user)                         # materials issued
        wo2 = self.released_wo(1)
        pr.cancel_work_order(wo2, self.user)
        wo2.refresh_from_db()
        self.assertEqual(wo2.status, "cancelled")

    def test_final_qc_defers_finished_goods(self):
        ItemProfile.objects.create(variant=self.shirt, qc_required_on_receipt=True)
        wo = self.released_wo(10)
        pr.issue_materials(wo, user=self.user)
        cut, stitch = wo.job_cards.order_by("sequence")
        pr.start_job_card(cut, user=self.user)
        pr.complete_job_card(cut, 10, 0, user=self.user)
        pr.start_job_card(stitch, user=self.user)
        pr.complete_job_card(stitch, 10, 0, user=self.user)
        qc.complete_check(qc.pending_checks(self.company, "in_process").get(), self.user, accepted=10, rejected=0)
        receipt = pr.receive_finished_goods(wo, 10, user=self.user)
        self.assertEqual(receipt.status, "qc_pending")
        self.assertEqual(st.available_qty(self.shirt, self.fg), D(0))
        with self.assertRaises(ERPError):
            pr.close_work_order(wo, self.user)                          # waiting for final QC
        qc.complete_check(qc.pending_checks(self.company, "final").get(), self.user, accepted=9, rejected=1, remarks="colour shade")
        self.assertEqual(st.available_qty(self.shirt, self.fg), D(9))
        pr.close_work_order(wo, self.user)
        wo.refresh_from_db()
        self.assertEqual(wo.variance_written_off, D("290.00"))          # the rejected unit's cost

    def test_operator_and_machine_reports(self):
        op = hr.create_employee(self.company, "Stitcher", date(2026, 1, 1))
        wo = self.released_wo(10)
        pr.issue_materials(wo, user=self.user)
        cut = wo.job_cards.order_by("sequence").first()
        pr.start_job_card(cut, operator=op, user=self.user)
        pr.complete_job_card(cut, 10, 0, minutes=250, user=self.user)
        perf = reports.operator_performance(self.company)
        self.assertEqual(perf[0]["completed"], D(10))
        self.assertEqual(perf[0]["efficiency_percent"], D("120.00"))   # standard 300 min done in 250
