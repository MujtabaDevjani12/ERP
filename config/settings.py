"""
Project settings
-----------------
Root config for the multi-business POS (Gold/Diamond, Mart, Restaurant,
Clothing — see tenants.Store.business_type). Kept as one settings.py, same
as your WACRM project, since a small/medium POS doesn't need split
settings files.
"""
from pathlib import Path
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DEBUG", "True") == "True"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

# Behind nginx with HTTPS (needed for "Sign in with Google": Google only accepts https:// addresses on a real domain, not http://IP).
# .env:  CSRF_TRUSTED_ORIGINS=https://erp.example.com,https://owner.example.com   and   BEHIND_HTTPS_PROXY=True
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
if os.environ.get("BEHIND_HTTPS_PROXY", "False") == "True":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

# Each app owns its own models/urls — same pattern as wacrm-backend-multitenant.
# Order matters: an app must come after anything it has a ForeignKey to.
LOCAL_APPS = [
    "core",             # shared base models/helpers, no own tables
    "company",          # ERP: Company (legal entity above Store/branch)
    "tenants",          # Store, Warehouse — everything else FKs to these
    "accounts",         # custom User + roles
    "numbering",        # ERP: per company/branch/fiscal-year document numbers
    "audit",            # ERP: immutable audit trail (user/IP/old->new)
    "attachments",      # ERP: secure file attachments on any document
    "configuration",    # POS Configuration / System Configuration / numbering prefixes
    "pricing",          # Tax, Discount, CustomerGroup
    "catalog",          # Category, Brand, Unit, Product, Variant, Barcode
    "inventory",        # StockItem, StockAdjustment, StockTransfer, Rack
    "suppliers",        # Supplier, PurchaseOrder, PurchasePayment
    "customers",        # Customer (CNIC), LoyaltyTransaction, GiftCard
    "devices",          # Printer / scanner / scale, media library
    "register",         # POS terminal + open/close cash sessions
    "sales",            # Sale, SaleItem, SaleReturn, Delivery
    "payments",         # Payment, Refund
    "expenses",         # ExpenseCategory, Expense
    "documents",        # Invoice/email templates + send log (PDF/email/WhatsApp)
    "dashboard",        # server-rendered UI (POS screen, product pages, etc.)
    "reports",          # sales/purchase/stock reports + chart data, no models
    "billing",          # SaaS, ERP side: sign-up, the company's own Billing page, plan limits, the lock until a plan is paid
    "ownerconsole",     # SaaS, owner side: a SEPARATE site + database (plans, subscriptions, invoices, payments, gateways, e-mail)
]

# ERP modules (accounting, stock ledger, procurement, production, wholesale, HR/payroll, POS upgrade, reports, API).
# config/erp_apps.py lists them in dependency order and skips any that are missing from disk, so the original POS
# keeps starting even if you delete or have not yet copied a module.
from config import erp_apps  # noqa: E402

LOCAL_APPS = LOCAL_APPS + erp_apps.installed_erp_apps()

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

AUTH_USER_MODEL = "accounts.User"

# Super Admins (is_superuser or the "Super Admin" role) pass every permission check;
# everyone else is governed by their roles' permissions (Django's ModelBackend).
AUTHENTICATION_BACKENDS = ["rbac.backends.SuperAdminBackend"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "audit.middleware.AuditContextMiddleware",     # user/IP for audit rows
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "tenants.middleware.CurrentStoreMiddleware",   # attaches request.store
    "company.middleware.CurrentCompanyMiddleware", # attaches request.company
    "billing.middleware.SubscriptionMiddleware",    # plan modules + expired/suspended lock (never touches the platform owner)
    "register.middleware.ActiveRegisterMiddleware",  # redirects POS to "open register" if none is open
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tenants.context_processors.current_store",
                "configuration.context_processors.pos_settings",
            ],
        },
    },
]

# The application shell (sidebar, notifications, command palette data) - see core/context_processors.py.
TEMPLATES[0]["OPTIONS"]["context_processors"].append("core.context_processors.ui_shell")
TEMPLATES[0]["OPTIONS"]["context_processors"].append("billing.context_processors.billing_shell")

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --- Database ---------------------------------------------------------
# SQLite by default (fine for a single shop / development). Point
# DATABASE_URL at Postgres later for multi-branch / higher traffic use —
# the rest of the code doesn't need to change.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# --- Owner console: its own site, its own sign-in and its own database ---------
# The ERP owner's console (plans, companies, payments, gateway keys, e-mail) is NOT part of the ERP. It lives in ownerconsole/ and
# runs as a second site:  python manage.py runserver 8001 --settings=config.owner_settings
# Its data sits in owner.sqlite3 (alias "owner"). OWNER_DB_SAME=True keeps it in the ERP database instead (the test-suite does this).
import sys  # noqa: E402

OWNER_DB_ALIAS = "default" if ("test" in sys.argv or os.environ.get("OWNER_DB_SAME", "False") == "True") else "owner"
if OWNER_DB_ALIAS == "owner":
    DATABASES["owner"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(os.environ.get("OWNER_DB_PATH", "")) if os.environ.get("OWNER_DB_PATH") else BASE_DIR / "owner.sqlite3",
    }
DATABASE_ROUTERS = ["ownerconsole.router.OwnerRouter"]
OWNER_COOKIE_NAME = "owner_auth"                      # the signed sign-in cookie of the owner console
OWNER_SESSION_HOURS = 12                              # sign-in lasts this long ...
OWNER_REMEMBER_DAYS = 14                              # ... or this long with "Keep me signed in"
OWNER_RESET_HOURS = 2                                 # forgot-password links
OWNER_INVITE_HOURS = 48                               # links e-mailed to a new owner-team member
OWNER_CONSOLE_URL = os.environ.get("OWNER_CONSOLE_URL", "http://127.0.0.1:8001")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "Asia/Karachi")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# ERP: secure attachments (PDF/JPG/PNG/DOCX/XLSX) size limit, in MB.
ATTACHMENT_MAX_MB = int(os.environ.get("ATTACHMENT_MAX_MB", "10"))
# Only set True behind a reverse proxy you control (trusts X-Forwarded-For for audit IPs).
AUDIT_TRUST_PROXY_HEADERS = os.environ.get("AUDIT_TRUST_PROXY_HEADERS", "False") == "True"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "dashboard:pos"
LOGOUT_REDIRECT_URL = "accounts:login"

# --- Email (invoices) ---------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend" if os.environ.get("EMAIL_HOST") else "django.core.mail.backends.console.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "care@example.com")

# --- WhatsApp Cloud API (Meta) — used by documents.services.send_whatsapp_invoice
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")

# Business-type choices shared across the project (tenants.Store, catalog UI wording).
BUSINESS_TYPES = [
    ("gold_diamond", "Gold & Diamond"),
    ("mart", "Mart / Grocery"),
    ("restaurant", "Restaurant"),
    ("clothing", "Clothing / Fashion"),
    ("general", "General Retail"),
]


# --- ERP settings -------------------------------------------------------
# Weekly off days for attendance/payroll: Monday=0 ... Sunday=6. Default: Sunday.
HR_WEEKLY_OFF_DAYS = tuple(int(d) for d in os.environ.get("HR_WEEKLY_OFF_DAYS", "6").split(",") if d.strip() != "")
# Extra raw material (in %) that may be issued to a work order over the BOM requirement. 0 = none.
PRODUCTION_ISSUE_TOLERANCE_PERCENT = int(os.environ.get("PRODUCTION_ISSUE_TOLERANCE_PERCENT", "0"))
# Wholesale discount above this % needs a manager's approval when no approval workflow is configured.
SALES_MAX_DISCOUNT_PERCENT = int(os.environ.get("SALES_MAX_DISCOUNT_PERCENT", "10"))
# Errors go to the console / service log even when DEBUG is off, so `journalctl -u erp` / `-u owner-console` shows the real traceback
# behind an "Internal Server Error" page (Django's default prints it only in DEBUG mode).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

# E-mail notifications (in-app notifications are always on). Needs the EMAIL_* settings above.
NOTIFY_BY_EMAIL = os.environ.get("NOTIFY_BY_EMAIL", "False") == "True"

# Optional REST API (only used when `djangorestframework` is installed).
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",    # use only over HTTPS
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"user": "600/hour"},
}
