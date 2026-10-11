"""Deliveries: create from a sale, status flow, branch scoping, permissions, notes."""
import datetime

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld, grant
from customers.models import Customer
from sales.models import Delivery, Sale
from tenants.models import Store, Warehouse


class DeliveryTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.cust = Customer.objects.create(store=self.w.store, name="Ali Khan", phone="0300 1234567", email="ali@example.com", address="12 Mall Road")
        self.sale = Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, reference="S-1", biller=self.w.manager,
                                        customer=self.cust, status="completed", total_amount=100)
        self.client.login(username="manager", password=PASSWORD)

    def make(self, **kw):
        data = {"sale_ref": "S-1", "recipient_name": "Ali Khan", "phone": "0300", "address": "12 Mall Road", "city": "Lahore",
                "scheduled_date": "2030-01-05", "rider": "TCS", "tracking_no": "T1", "note": ""}
        data.update(kw)
        return self.client.post(reverse("sales:delivery_add"), data)

    def test_form_prefills_from_customer(self):
        r = self.client.get(reverse("sales:delivery_add") + f"?sale={self.sale.pk}")
        self.assertContains(r, "Ali Khan")
        self.assertContains(r, "12 Mall Road")

    def test_create_by_invoice_number(self):
        r = self.make()
        d = Delivery.objects.get()
        self.assertRedirects(r, reverse("sales:delivery_detail", args=[d.pk]))
        self.assertEqual((d.sale, d.status, d.rider, d.created_by), (self.sale, "pending", "TCS", self.w.manager))
        self.assertTrue(d.reference)

    def test_one_delivery_per_sale_and_only_completed_sales(self):
        self.make()
        r = self.make()
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertContains(r, "cannot be delivered")
        held = Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, reference="S-2", biller=self.w.manager, status="held")
        r = self.make(sale_ref="S-2")
        self.assertContains(r, "cannot be delivered")
        self.assertContains(self.make(sale_ref="NOPE"), "No sale with this invoice number")

    def test_address_and_recipient_required(self):
        r = self.make(address="", recipient_name="")
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertContains(r, "required")

    def test_status_flow(self):
        self.make()
        d = Delivery.objects.get()
        url = reverse("sales:delivery_status", args=[d.pk])
        self.client.post(url, {"status": "out_for_delivery"})
        d.refresh_from_db(); self.assertEqual(d.status, "out_for_delivery")
        self.client.post(url, {"status": "delivered", "received_by": "Brother"})
        d.refresh_from_db()
        self.assertEqual((d.status, d.received_by), ("delivered", "Brother"))
        self.assertIsNotNone(d.delivered_at)
        # closed -> an ordinary user cannot reopen it
        cashier = self.w.cashier
        grant(cashier, "sales.view_delivery", "sales.change_delivery")
        self.client.logout(); self.client.login(username="cashier", password=PASSWORD)
        r = self.client.post(url, {"status": "pending"}, follow=True)
        d.refresh_from_db(); self.assertEqual(d.status, "delivered")
        self.assertContains(r, "cannot be changed")

    def test_superuser_can_reopen_and_delete_rules(self):
        self.make()
        d = Delivery.objects.get()
        self.client.post(reverse("sales:delivery_status", args=[d.pk]), {"status": "delivered"})
        self.client.post(reverse("sales:delivery_status", args=[d.pk]), {"status": "pending"})
        d.refresh_from_db(); self.assertEqual(d.status, "pending"); self.assertIsNone(d.delivered_at)
        self.client.post(reverse("sales:delivery_status", args=[d.pk]), {"status": "out_for_delivery"})
        self.client.post(reverse("sales:delivery_delete", args=[d.pk]))
        self.assertEqual(Delivery.objects.count(), 1)               # in progress -> cannot delete
        self.client.post(reverse("sales:delivery_status", args=[d.pk]), {"status": "cancelled"})
        self.client.post(reverse("sales:delivery_delete", args=[d.pk]))
        self.assertEqual(Delivery.objects.count(), 0)

    def test_bad_status_ignored(self):
        self.make(); d = Delivery.objects.get()
        self.client.post(reverse("sales:delivery_status", args=[d.pk]), {"status": "teleported"})
        d.refresh_from_db(); self.assertEqual(d.status, "pending")

    def test_list_filters_and_kpis(self):
        self.make()
        r = self.client.get(reverse("sales:delivery_list"))
        self.assertContains(r, "S-1"); self.assertEqual(r.context["n_packing"], 1)
        self.assertContains(self.client.get(reverse("sales:delivery_list") + "?status=delivered"), "No deliveries match")
        self.assertContains(self.client.get(reverse("sales:delivery_list") + "?q=T1"), "Ali Khan")
        self.assertEqual(self.client.get(reverse("sales:delivery_list") + "?df=2031-01-01").context["result_count"], 0)

    def test_late_counter(self):
        self.make(scheduled_date="2020-01-01")
        self.assertEqual(self.client.get(reverse("sales:delivery_list")).context["n_late"], 1)

    def test_other_branch_cannot_see_it(self):
        self.make(); d = Delivery.objects.get()
        other = Store.objects.create(name="Other", company=self.w.company)
        Warehouse.objects.create(store=other, name="W", is_default=True)
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_superuser("boss2", "b@example.com", PASSWORD, store=other)
        self.client.logout(); self.client.login(username="boss2", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("sales:delivery_detail", args=[d.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("sales:delivery_list")).context["result_count"], 0)

    def test_permissions(self):
        self.make(); d = Delivery.objects.get()
        self.client.logout(); self.client.login(username="cashier", password=PASSWORD)       # cashier has no delivery rights
        for url in (reverse("sales:delivery_list"), reverse("sales:delivery_detail", args=[d.pk]), reverse("sales:delivery_add")):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("sales:delivery_status", args=[d.pk]), {"status": "delivered"}).status_code, 403)

    def test_note_page_and_email(self):
        self.make(); d = Delivery.objects.get()
        self.assertContains(self.client.get(reverse("sales:delivery_note", args=[d.pk])), "Delivered by")
        self.client.post(reverse("sales:delivery_email", args=[d.pk]), {"email": "ali@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.sale.reference, mail.outbox[0].body)
        self.client.post(reverse("sales:delivery_email", args=[d.pk]), {"email": "not-an-email"})
        self.assertEqual(len(mail.outbox), 1)

    def test_whatsapp_link_uses_country_code(self):
        self.make(phone="0300 1234567"); d = Delivery.objects.get()
        r = self.client.get(reverse("sales:delivery_detail", args=[d.pk]))
        self.assertContains(r, "https://wa.me/92300")


class PosShareLinksTests(TestCase):
    def test_success_response_offers_email_whatsapp_and_delivery(self):
        import json
        from register import services
        w = PosWorld()
        cust = Customer.objects.create(store=w.store, name="Ali", phone="0300 1234567", email="ali@example.com")
        self.client.login(username="manager", password=PASSWORD)
        session = services.open_session(w.counter1, w.manager, 100)
        s = self.client.session; s["active_register_session_id"] = session.pk; s.save()
        body = {"items": [{"variant_id": w.milk.pk, "quantity": "1"}], "customer_id": cust.pk, "payments": [{"method": "cash", "amount": "50"}]}
        data = self.client.post(reverse("sales:pos_finalize"), data=json.dumps(body), content_type="application/json").json()
        self.assertTrue(data["ok"], data)
        self.assertTrue(data["whatsapp_url"].startswith("https://wa.me/"), data["whatsapp_url"])
        self.assertEqual(data["delivery_url"], reverse("sales:delivery_add") + f"?sale={data['sale_id']}")
        self.assertEqual(data["email_url"], reverse("documents:send_email", args=[data["sale_id"]]))
        Delivery.objects.create(sale_id=data["sale_id"], reference="D-1", address="x")
        again = self.client.post(reverse("sales:pos_finalize"), data=json.dumps({**body, "checkout_token": "t1"}), content_type="application/json").json()
        self.assertTrue(again["ok"])
