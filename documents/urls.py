from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("sales/<int:pk>/invoice.pdf", views.invoice_pdf_view, name="invoice_pdf"),
    path("sales/<int:pk>/send-email/", views.send_email_view, name="send_email"),
    path("sales/<int:pk>/send-whatsapp/", views.send_whatsapp_view, name="send_whatsapp"),
    path("logs/", views.document_log_list, name="document_log_list"),
]
