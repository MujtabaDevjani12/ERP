import datetime
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld
from suppliers.models import PurchaseOrder, PurchaseOrderItem, PurchasePayment, Supplier


class PurchaseListFilterTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        sup = Supplier.objects.create(store=self.w.store, name="Acme")
        def po(ref, paid, day):
            o = PurchaseOrder.objects.create(reference=ref, supplier=sup, warehouse=self.w.warehouse, status="received", order_date=datetime.date(2026, 10, day))
            PurchaseOrderItem.objects.create(purchase_order=o, variant=self.w.tea, quantity=Decimal("10"), cost_price=Decimal("10"))   # 100
            if paid:
                PurchasePayment.objects.create(reference=f"PP-{ref}", purchase_order=o, amount=Decimal(paid), paid_on=datetime.date(2026, 10, day))
            return o
        po("PO-PAID", "100", 1); po("PO-PART", "40", 2); po("PO-DUE", None, 3)
        self.client.login(username="manager", password=PASSWORD)

    def refs(self, **params):
        return sorted(o.reference for o in self.client.get(reverse("suppliers:purchase_list"), params).context["orders"])

    def test_payment_filter(self):
        self.assertEqual(self.refs(pay="paid"), ["PO-PAID"])
        self.assertEqual(self.refs(pay="partial"), ["PO-PART"])
        self.assertEqual(self.refs(pay="due"), ["PO-DUE"])
        self.assertEqual(len(self.refs()), 3)

    def test_date_and_warehouse_filters(self):
        self.assertEqual(self.refs(df="2026-10-02", dt="2026-10-02"), ["PO-PART"])
        self.assertEqual(len(self.refs(warehouse=self.w.warehouse.pk)), 3)
        self.assertEqual(self.refs(warehouse=self.w.warehouse.pk + 999), [])
