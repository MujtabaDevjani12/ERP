"""Stock transfers: create / send / receive / cancel, stock movement, shortage safety, branch scoping, permissions."""
import json
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld, grant
from inventory import transfers as svc
from inventory.models import StockItem, StockTransfer
from tenants.models import Store, Warehouse


class TransferTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.back = Warehouse.objects.create(store=self.w.store, name="Back room")
        self.client.login(username="manager", password=PASSWORD)

    def qty(self, variant, wh):
        row = StockItem.objects.filter(variant=variant, warehouse=wh).first()
        return row.quantity if row else Decimal("0")

    def post_new(self, lines, mode="pending", **kw):
        data = {"from_warehouse": self.w.warehouse.pk, "to_warehouse": self.back.pk, "note": "n", "mode": mode,
                "lines_json": json.dumps([{"variant_id": v.pk, "quantity": q} for v, q in lines])}
        data.update(kw)
        return self.client.post(reverse("inventory:transfer_add"), data)

    def test_pending_moves_nothing(self):
        r = self.post_new([(self.w.tea, "4")])
        t = StockTransfer.objects.get()
        self.assertRedirects(r, reverse("inventory:transfer_detail", args=[t.pk]))
        self.assertEqual(t.status, "draft")
        self.assertEqual(self.qty(self.w.tea, self.w.warehouse), 10)
        self.assertTrue(t.reference)

    def test_send_then_receive(self):
        self.post_new([(self.w.tea, "4")])
        t = StockTransfer.objects.get()
        self.client.post(reverse("inventory:transfer_send", args=[t.pk]))
        t.refresh_from_db()
        self.assertEqual((t.status, self.qty(self.w.tea, self.w.warehouse), self.qty(self.w.tea, self.back)), ("in_transit", 6, 0))
        self.assertIsNotNone(t.sent_at)
        self.client.post(reverse("inventory:transfer_receive", args=[t.pk]))
        t.refresh_from_db()
        self.assertEqual((t.status, self.qty(self.w.tea, self.w.warehouse), self.qty(self.w.tea, self.back)), ("received", 6, 4))
        self.assertIsNotNone(t.received_at)

    def test_complete_now(self):
        self.post_new([(self.w.tea, "3"), (self.w.milk, "2")], mode="complete")
        t = StockTransfer.objects.get()
        self.assertEqual(t.status, "received")
        self.assertEqual((self.qty(self.w.tea, self.back), self.qty(self.w.milk, self.back)), (3, 2))
        self.assertEqual((self.qty(self.w.tea, self.w.warehouse), self.qty(self.w.milk, self.w.warehouse)), (7, 3))

    def test_shortage_blocks_and_leaves_nothing_half_moved(self):
        r = self.post_new([(self.w.milk, "2"), (self.w.tea, "99")], mode="send")
        self.assertEqual(StockTransfer.objects.count(), 0)                 # the whole create rolled back
        self.assertEqual((self.qty(self.w.milk, self.w.warehouse), self.qty(self.w.tea, self.w.warehouse)), (5, 10))
        self.assertContains(r, "Not enough")

    def test_cancel_in_transit_returns_stock(self):
        self.post_new([(self.w.tea, "4")], mode="send")
        t = StockTransfer.objects.get()
        self.assertEqual(self.qty(self.w.tea, self.w.warehouse), 6)
        self.client.post(reverse("inventory:transfer_cancel", args=[t.pk]))
        t.refresh_from_db()
        self.assertEqual((t.status, self.qty(self.w.tea, self.w.warehouse), self.qty(self.w.tea, self.back)), ("cancelled", 10, 0))

    def test_cancel_pending_and_delete(self):
        self.post_new([(self.w.tea, "4")])
        t = StockTransfer.objects.get()
        self.client.post(reverse("inventory:transfer_cancel", args=[t.pk]))
        t.refresh_from_db(); self.assertEqual(t.status, "cancelled")
        self.client.post(reverse("inventory:transfer_delete", args=[t.pk]))
        self.assertEqual(StockTransfer.objects.count(), 0)

    def test_cannot_delete_or_resend_after_it_moved(self):
        self.post_new([(self.w.tea, "4")], mode="send")
        t = StockTransfer.objects.get()
        self.client.post(reverse("inventory:transfer_delete", args=[t.pk]))
        self.assertEqual(StockTransfer.objects.count(), 1)
        self.client.post(reverse("inventory:transfer_send", args=[t.pk]))   # already in transit: must not take stock twice
        self.assertEqual(self.qty(self.w.tea, self.w.warehouse), 6)
        self.client.post(reverse("inventory:transfer_receive", args=[t.pk]))
        self.client.post(reverse("inventory:transfer_receive", args=[t.pk]))  # a double click must not add it twice
        self.assertEqual(self.qty(self.w.tea, self.back), 4)
        self.client.post(reverse("inventory:transfer_cancel", args=[t.pk]))   # completed: cannot be cancelled
        t.refresh_from_db(); self.assertEqual(t.status, "received")

    def test_same_warehouse_and_bad_input_rejected(self):
        r = self.post_new([(self.w.tea, "1")], to_warehouse=self.w.warehouse.pk)
        self.assertContains(r, "two different warehouses")
        self.assertContains(self.post_new([(self.w.tea, "0")]), "more than zero")
        self.assertContains(self.post_new([]), "at least one product")
        self.assertContains(self.post_new([], lines_json="not json"), "Could not read")
        self.assertEqual(StockTransfer.objects.count(), 0)

    def test_duplicate_lines_are_merged(self):
        self.post_new([(self.w.tea, "2"), (self.w.tea, "3")])
        self.assertEqual(StockTransfer.objects.get().items.get().quantity, 5)

    def test_other_branch_data_is_invisible(self):
        other = Store.objects.create(name="Other", company=self.w.company)
        ow = Warehouse.objects.create(store=other, name="Elsewhere")
        t = StockTransfer.objects.create(reference="TR-X", from_warehouse=ow, to_warehouse=ow)
        self.assertEqual(self.client.get(reverse("inventory:transfer_detail", args=[t.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("inventory:transfer_send", args=[t.pk])).status_code, 404)
        with self.assertRaises(svc.TransferError):
            svc.create_transfer(store=self.w.store, user=self.w.manager, from_warehouse=self.w.warehouse, to_warehouse=ow, lines=[(self.w.tea.pk, 1)])

    def test_permissions(self):
        self.client.logout(); self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("inventory:transfer_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("inventory:transfer_add")).status_code, 403)
        grant(self.w.cashier, "inventory.view_stocktransfer")
        self.assertEqual(self.client.get(reverse("inventory:transfer_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("inventory:transfer_add")).status_code, 403)

    def test_pages_render_and_filter(self):
        self.post_new([(self.w.tea, "4")], mode="send")
        t = StockTransfer.objects.get()
        r = self.client.get(reverse("inventory:transfer_list"))
        self.assertContains(r, t.reference)
        self.assertContains(self.client.get(reverse("inventory:transfer_list") + "?status=draft"), "No transfers match")
        self.assertContains(self.client.get(reverse("inventory:transfer_detail", args=[t.pk])), "Mark received")
        self.assertContains(self.client.get(reverse("inventory:transfer_add")), "New stock transfer")

    def test_lookup_shows_source_stock(self):
        r = self.client.get(reverse("inventory:transfer_lookup"), {"q": "TEA", "warehouse": self.w.warehouse.pk})
        row = r.json()["results"][0]
        self.assertEqual((row["sku"], row["stock"]), ("TEA-1", "10"))
        r = self.client.get(reverse("inventory:transfer_lookup"), {"q": "BCTEA", "warehouse": self.back.pk})
        self.assertEqual(r.json()["results"][0]["stock"], "0")


class WarehouseTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)
        self.ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def test_list_and_drawer(self):
        r = self.client.get(reverse("inventory:warehouse_list"))
        self.assertContains(r, "Floor"); self.assertContains(r, "Default")
        self.assertContains(self.client.get(reverse("inventory:warehouse_add"), **self.ajax), "Warehouse name")
        self.assertRedirects(self.client.get(reverse("inventory:warehouse_add")), reverse("inventory:warehouse_list"))

    def test_add_edit_and_unique_name(self):
        r = self.client.post(reverse("inventory:warehouse_add"), {"name": "Back room", "code": "B1", "warehouse_type": "GENERAL"}, **self.ajax)
        self.assertEqual(r.json()["ok"], True)
        back = Warehouse.objects.get(name="Back room")
        self.assertEqual((back.store, back.is_default), (self.w.store, False))
        dup = self.client.post(reverse("inventory:warehouse_add"), {"name": "back ROOM", "warehouse_type": "GENERAL"}, **self.ajax)
        self.assertContains(dup, "already has a warehouse")
        self.client.post(reverse("inventory:warehouse_edit", args=[back.pk]), {"name": "Back store", "code": "B2", "warehouse_type": "OUTLET"}, **self.ajax)
        back.refresh_from_db(); self.assertEqual((back.name, back.warehouse_type), ("Back store", "OUTLET"))

    def test_default_is_unique_and_cannot_be_unset(self):
        self.client.post(reverse("inventory:warehouse_add"), {"name": "Depot", "warehouse_type": "GENERAL", "is_default": "on"}, **self.ajax)
        depot = Warehouse.objects.get(name="Depot")
        self.assertEqual(list(Warehouse.objects.filter(store=self.w.store, is_default=True)), [depot])
        r = self.client.post(reverse("inventory:warehouse_edit", args=[depot.pk]), {"name": "Depot", "warehouse_type": "GENERAL"}, **self.ajax)
        self.assertContains(r, "needs one")
        self.client.post(reverse("inventory:warehouse_default", args=[self.w.warehouse.pk]))
        self.assertEqual(list(Warehouse.objects.filter(store=self.w.store, is_default=True)), [self.w.warehouse])

    def test_other_branch_and_permissions(self):
        other = Store.objects.create(name="Other", company=self.w.company)
        ow = Warehouse.objects.create(store=other, name="Elsewhere")
        self.assertEqual(self.client.get(reverse("inventory:warehouse_edit", args=[ow.pk]), **self.ajax).status_code, 404)
        self.assertEqual(self.client.post(reverse("inventory:warehouse_default", args=[ow.pk])).status_code, 404)
        self.client.logout(); self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("inventory:warehouse_list")).status_code, 403)
        self.assertEqual(self.client.post(reverse("inventory:warehouse_add"), {"name": "X"}, **self.ajax).status_code, 403)
