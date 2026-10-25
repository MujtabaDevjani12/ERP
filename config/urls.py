"""
URL layout
----------
/                       dashboard home
/pos/                   the POS billing screen (dashboard app)
/register/open/         open-register gate before POS is usable
/register/close/        close-register (counted vs expected cash)
/register/counters/     counters (POS terminals) management
/payments/methods/      payment methods settings
/accounts/login|logout  auth
/products/...           catalog app — products, variants, categories, brands, units, attributes
/inventory/...          stock levels, quantity adjustment (scan), racks
/suppliers/...          suppliers + purchase orders
/customers/...          customers, gift cards
/pricing/...            tax, discounts, price/customer groups
/devices/...            printers, scanners, scales
/expenses/...           expenses + categories
/sales/...              sale list/detail, POS finalize endpoint, add payment
/documents/...          invoice PDF / email / WhatsApp delivery + send log
/settings/...           POS Configuration / System Configuration / numbering (configuration app)
/reports/...            sales/purchase/stock reports + chart data
/erp/                   ERP dashboard + report pages (Excel/PDF)     /notifications/  alerts inbox     /api/v1/  read-only REST API
/plans/ /signup/ /billing/   sign-up and each company's own Billing page   (the owner console is a separate site: config/owner_urls.py)
/admin/                 Django admin — also today's home for Roles (Groups) & full data access
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("", include("billing.urls")),                      # /plans/ /signup/ /billing/ (each company's own billing)

    path("", include("dashboard.urls")),
    path("register/", include("register.urls")),
    path("payments/", include("payments.urls")),

    path("products/", include("catalog.urls")),
    path("inventory/", include("inventory.urls")),
    path("suppliers/", include("suppliers.urls")),
    path("customers/", include("customers.urls")),
    path("pricing/", include("pricing.urls")),
    path("devices/", include("devices.urls")),
    path("expenses/", include("expenses.urls")),
    path("sales/", include("sales.urls")),
    path("documents/", include("documents.urls")),
    path("settings/", include("configuration.urls")),
    path("reports/", include("reports.urls")),
]

# ERP: dashboard/reports (/erp/), notifications, read-only API. Only the modules that are installed.
from config.erp_apps import erp_urlpatterns  # noqa: E402

urlpatterns += erp_urlpatterns()

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])

admin.site.site_header = "ERP Control Center"
admin.site.site_title = "ERP Control Center"
admin.site.index_title = "Business modules"
