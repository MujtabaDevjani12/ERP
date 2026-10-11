"""Purchase returns: goods go back to the supplier, stock leaves the warehouse, what we owe goes down - all or nothing."""
import datetime
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld, grant
from inventory.models import StockItem
from suppliers.models import PurchaseOrder, PurchaseOrderItem, PurchaseReturn, Supplier
from tenants.models import Store, Warehouse


class PurchaseReturnTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.supplier = Supplier.objects.create(store=self.w.store, name="Acme Traders")
        self.po = PurchaseOrder.objects.create(reference="PO-1", supplier=self.supplier, warehouse=self.w.warehouse, status="received", order_date=datetime.date(2026, 10, 1))
        PurchaseOrderItem.objects.create(purchase_order=self.po, variant=self.w.tea, quantity=Decimal("6"), cost_price=Decimal("10"))
        PurchaseOrderItem.objects.create(purchase_order=self.po, variant=self.w.milk, quantity=Decimal("4"), cost_price=Decimal("20"))
        self.client.login(username="manager", password=PASSWORD)
        self.url = reverse("suppliers:purchase_return", args=[self.po.pk])      # stock: tea 10, milk 5

    def send(self, tea="", milk="", reason="damaged"):
        return self.client.post(self.url, {f"qty_{self.w.tea.pk}": tea, f"qty_{self.w.milk.pk}": milk, "reason": reason})

    def test_return_moves_stock_and_lowers_what_is_owed(self):
        self.assertEqual(self.po.due_amount, 140)                                 # 6x10 + 4x20
        r = self.send(tea="2", milk="1")
        self.assertRedirects(r, reverse("suppliers:purchase_detail", args=[self.po.pk]))
        ret = PurchaseReturn.objects.get()
        self.assertEqual((ret.purchase_order, ret.reason, ret.created_by, bool(ret.reference)), (self.po, "damaged", self.w.manager, True))
        self.assertEqual(sorted((i.variant.sku, i.quantity, i.cost_price) for i in ret.items.all()), [("MILK-1", 1, 20), ("TEA-1", 2, 10)])
        self.assertEqual((self.w.stock_of(self.w.tea), self.w.stock_of(self.w.milk)), (8, 4))
        self.po = PurchaseOrder.objects.get(pk=self.po.pk)
        self.assertEqual((self.po.returned_amount, self.po.due_amount), (40, 100))

    def test_cannot_send_back_more_than_was_received_or_than_is_in_stock(self):
        self.assertContains(self.send(tea="7"), "only 6 can still be returned")
        StockItem.objects.filter(variant=self.w.tea).update(quantity=1)           # most of it was already sold
        self.assertContains(self.send(tea="3", milk="1"), "only 1 is in Floor now")
        self.assertEqual(PurchaseReturn.objects.count(), 0)                        # all or nothing: the milk was not taken either
        self.assertEqual(self.w.stock_of(self.w.milk), 5)

    def test_second_return_only_gets_what_is_left(self):
        self.send(tea="4")
        self.assertContains(self.send(tea="3"), "only 2 can still be returned")
        self.send(tea="2")
        self.assertEqual(PurchaseReturn.objects.count(), 2)
        self.assertEqual(self.w.stock_of(self.w.tea), 4)

    def test_validation(self):
        self.assertContains(self.send(), "at least one product")
        self.assertContains(self.send(tea="1", reason=" "), "reason is required")
        self.assertContains(self.send(tea="abc"), "not a number")
        self.assertContains(self.send(tea="-1"), "not valid")
        self.assertContains(self.send(tea="nan"), "not valid")
        self.assertEqual(PurchaseReturn.objects.count(), 0)

    def test_only_received_orders_and_not_erp_orders(self):
        self.po.status = "ordered"; self.po.save()
        self.assertRedirects(self.client.get(self.url), reverse("suppliers:purchase_detail", args=[self.po.pk]))
        self.po.status = "received"; self.po.save()
        with mock.patch("suppliers.purchase_returns.managed_by_erp", return_value=True), mock.patch("suppliers.views._managed_by_erp", return_value=True):
            self.assertRedirects(self.client.get(self.url), reverse("suppliers:purchase_detail", args=[self.po.pk]))
            self.send(tea="1")
        self.assertEqual(PurchaseReturn.objects.count(), 0)

    def test_scoping_and_permissions(self):
        other = Store.objects.create(name="Other", company=self.w.company)
        ow = Warehouse.objects.create(store=other, name="O")
        theirs = PurchaseOrder.objects.create(reference="PO-X", supplier=Supplier.objects.create(store=other, name="S"), warehouse=ow, status="received", order_date=datetime.date(2026, 10, 1))
        self.assertEqual(self.client.get(reverse("suppliers:purchase_return", args=[theirs.pk])).status_code, 404)
        self.client.logout(); self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        grant(self.w.cashier, "suppliers.view_purchaseorder")
        self.assertEqual(self.client.get(self.url).status_code, 403)               # may view orders, still may not return goods

    def test_detail_page_shows_the_return_and_the_button(self):
        page = self.client.get(reverse("suppliers:purchase_detail", args=[self.po.pk]))
        self.assertContains(page, "Return to supplier")
        self.send(tea="6", milk="4")                                               # everything goes back
        page = self.client.get(reverse("suppliers:purchase_detail", args=[self.po.pk]))
        self.assertContains(page, "Returned to the supplier"); self.assertContains(page, "damaged")
        self.assertNotContains(page, "Return to supplier</span>")                  # nothing left to return: no button
        self.assertEqual(PurchaseOrder.objects.get(pk=self.po.pk).due_amount, 0)
