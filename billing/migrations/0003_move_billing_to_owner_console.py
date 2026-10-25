"""
The plans / subscriptions / invoices / payments / platform settings move to the separate owner database (ownerconsole app).

Nothing is lost: before the old tables are dropped, whatever they hold is written to legacy_billing_export.json next to manage.py.
`python manage.py setup_owner` imports that file into the owner database (and renames it to .imported).
"""
import datetime
import json
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.db import migrations, models


def _plain(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "name") and hasattr(value, "storage"):          # a FieldFile
        return value.name or ""
    return value


def _rows(model):
    out = []
    for obj in model.objects.all().order_by("pk"):
        out.append({f.attname: _plain(getattr(obj, f.attname)) for f in model._meta.concrete_fields})
    return out


def export_legacy(apps, schema_editor):
    models = {name: apps.get_model("billing", name) for name in ("Plan", "PlatformSettings", "Subscription", "Invoice", "Payment", "BillingEvent")}
    if not any(m.objects.exists() for m in models.values()):
        return
    Company = apps.get_model("company", "Company")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    users = {u["id"]: u["username"] for u in User.objects.values("id", "username")}
    companies = {c.pk: {"name": c.name, "email": getattr(c, "email", "") or "", "phone": getattr(c, "phone", "") or ""} for c in Company.objects.all()}
    data = {name.lower(): _rows(m) for name, m in models.items()}
    data["companies"] = companies
    data["users"] = users
    path = Path(settings.BASE_DIR) / "legacy_billing_export.json"
    try:
        path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        print(f"\n  billing: old plans / subscriptions / payments saved to {path.name} - run 'python manage.py setup_owner' to import them.")
    except OSError as exc:                                              # pragma: no cover
        print(f"\n  billing: WARNING could not write {path}: {exc}")


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0002_google_client_secret"),
        ("company", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(export_legacy, migrations.RunPython.noop),
        migrations.DeleteModel(name="Payment"),
        migrations.DeleteModel(name="Invoice"),
        migrations.DeleteModel(name="BillingEvent"),
        migrations.DeleteModel(name="PlatformOwner"),
        migrations.DeleteModel(name="Subscription"),
        migrations.DeleteModel(name="Plan"),
        migrations.DeleteModel(name="PlatformSettings"),
        migrations.CreateModel(
            name="BillingAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ],
            options={
                "permissions": [
                    ("view_subscription", "Can open Billing (plan, usage, invoices)"),
                    ("change_subscription", "Can change or cancel the plan"),
                    ("add_payment", "Can pay invoices"),
                    ("view_invoice", "Can view and print invoices"),
                    ("view_payment", "Can see payments"),
                    ("view_billingevent", "Can see billing activity"),
                ],
                "managed": False,
                "default_permissions": (),
            },
        ),
    ]
