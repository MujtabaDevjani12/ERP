"""WSGI entry point of the owner console (a separate site from the ERP)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.owner_settings")

application = get_wsgi_application()
