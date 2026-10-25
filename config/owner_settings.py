"""
Settings of the OWNER CONSOLE - a separate website from the ERP.

    python manage.py runserver 8001 --settings=config.owner_settings

Differences from the ERP (config/settings.py, which this file builds on):
  * its own URLconf (config/owner_urls.py): no ERP page can be opened on this site
  * its own cookies (owner_auth / owner_csrftoken / owner_sessionid), so signing in here never signs you in to the ERP and vice versa
  * no ERP sign-in, no ERP middleware; the owner signs in with an OwnerAccount (ownerconsole app, owner.sqlite3)
  * messages are kept in a cookie (no ERP session table needed)
"""
from .settings import *  # noqa: F401,F403
from .settings import BASE_DIR, INSTALLED_APPS, MIDDLEWARE as ERP_MIDDLEWARE, TEMPLATES  # noqa: F401

ROOT_URLCONF = "config.owner_urls"
# Django's admin needs ERP sign-in middleware and sessions; this site has no admin and its own sign-in, so the app is left out.
INSTALLED_APPS = [a for a in INSTALLED_APPS if a != "django.contrib.admin"]
WSGI_APPLICATION = "config.owner_wsgi.application"

SESSION_COOKIE_NAME = "owner_sessionid"
CSRF_COOKIE_NAME = "owner_csrftoken"
CSRF_COOKIE_HTTPONLY = False
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

# If the ERP serves its static files with WhiteNoise (production gunicorn), this site must too, or its CSS/JS give 404.
_STATIC_MIDDLEWARE = [m for m in ERP_MIDDLEWARE if "whitenoise" in m.lower()]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    *_STATIC_MIDDLEWARE,
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"
