"""The invoice PDF is drawn with ReportLab, so it must work without WeasyPrint / GTK (the Windows 'libgobject' error)."""
import json
import sys

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from core.pos_fixtures import PASSWORD, PosWorld
from customers.models import Customer
from documents.models import DocumentLog
from documents.services import render_invoice_pdf
from register import services
from sales.models import Sale


class InvoicePdfTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        cust = Customer.objects.create(store=self.w.store, name="Ali Raza", phone="0300 1234567", email="ali@example.com")
        self.client.login(username="manager", password=PASSWORD)
        session = services.open_session(self.w.counter1, self.w.manager, 100)
        s = self.client.session; s["active_register_session_id"] = session.pk; s.save()
        body = {"items": [{"variant_id": self.w.tea.pk, "quantity": "2"}, {"variant_id": self.w.milk.pk, "quantity": "1"}],
                "customer_id": cust.pk, "payments": [{"method": "cash", "amount": "100"}]}
        data = self.client.post(reverse("sales:pos_finalize"), data=json.dumps(body), content_type="application/json").json()
        self.assertTrue(data["ok"], data)
        self.sale = Sale.objects.get(pk=data["sale_id"])

    def test_pdf_bytes_without_weasyprint(self):
        pdf = render_invoice_pdf(self.sale)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1500)
        self.assertNotIn("weasyprint", sys.modules)

    def test_pdf_view_streams_the_file(self):
        r = self.client.get(reverse("documents:invoice_pdf", args=[self.sale.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertFalse(DocumentLog.objects.filter(channel="pdf_download", status="failed").exists())

    def test_email_attaches_the_pdf(self):
        self.client.post(reverse("documents:send_email", args=[self.sale.pk]), {"email": "ali@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        name, content, mime = mail.outbox[0].attachments[0]
        self.assertEqual(mime, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))

    def test_partial_payment_with_note_and_no_customer(self):
        self.sale.customer = None
        self.sale.sale_note = "Fragile — handle with care"
        self.sale.save()
        self.assertTrue(render_invoice_pdf(self.sale).startswith(b"%PDF"))
