"""One command to set the owner side up: create/upgrade owner.sqlite3, bring over data from the old billing tables, optionally make the first owner account."""
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from ownerconsole import db
from ownerconsole.models import BillingEvent, Invoice, OwnerAccount, Payment, PlatformSettings, Plan, Subscription

EXPORT = "legacy_billing_export.json"


def _dt(value):
    if not value:
        return None
    return parse_datetime(value) if isinstance(value, str) else value


def _dec(value):
    return Decimal(str(value)) if value not in (None, "") else None


def import_legacy(path, stdout=None):
    """Copy an export written by billing migration 0003 into the owner database. Returns counters."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    companies, users = data.get("companies", {}), data.get("users", {})
    counts = {"plans": 0, "subscriptions": 0, "invoices": 0, "payments": 0, "events": 0}
    with db.atomic():
        for row in data.get("platformsettings", [])[:1]:
            fields = {f.name for f in PlatformSettings._meta.concrete_fields} - {"id"}
            s = PlatformSettings.get()
            for k, v in row.items():
                if k in fields:
                    setattr(s, k, v)
            s.trial_days = 0 if s.trial_days == 14 else s.trial_days          # the old default gave every sign-up a free trial
            s.save()
        plan_map = {}
        for row in data.get("plan", []):
            plan, created = Plan.objects.get_or_create(slug=row["slug"], defaults={
                "name": row["name"], "tagline": row["tagline"], "description": row["description"], "price_monthly": _dec(row["price_monthly"]),
                "price_yearly": _dec(row["price_yearly"]), "currency": row["currency"], "modules": row["modules"], "max_users": row["max_users"],
                "max_stores": row["max_stores"], "max_products": row["max_products"], "max_monthly_sales": row["max_monthly_sales"],
                "features": row["features"], "trial_days": row["trial_days"], "is_active": row["is_active"], "is_public": row["is_public"],
                "is_featured": row["is_featured"], "sort_order": row["sort_order"]})
            plan_map[row["id"]] = plan
            counts["plans"] += int(created)
        sub_map = {}
        for row in data.get("subscription", []):
            if Subscription.objects.filter(company_id=row["company_id"]).exists() or row["plan_id"] not in plan_map:
                continue
            c = companies.get(str(row["company_id"]), {})
            sub = Subscription.objects.create(
                company_id=row["company_id"], company_name=(c.get("name") or f"Company {row['company_id']}")[:200], company_email=c.get("email", ""),
                company_phone=(c.get("phone") or "")[:40], plan=plan_map[row["plan_id"]], interval=row["interval"], status=row["status"],
                started_at=_dt(row["started_at"]), trial_ends_at=_dt(row["trial_ends_at"]), current_period_start=_dt(row["current_period_start"]),
                current_period_end=_dt(row["current_period_end"]), cancel_at_period_end=row["cancel_at_period_end"], cancelled_at=_dt(row["cancelled_at"]),
                suspended_reason=row["suspended_reason"], price_override=_dec(row["price_override"]), max_users_override=row["max_users_override"],
                max_stores_override=row["max_stores_override"], max_products_override=row["max_products_override"],
                max_monthly_sales_override=row["max_monthly_sales_override"], notes=row["notes"])
            sub_map[row["id"]] = sub
            counts["subscriptions"] += 1
        inv_map = {}
        for row in data.get("invoice", []):
            sub = sub_map.get(row["subscription_id"])
            if sub is None or Invoice.objects.filter(number=row["number"]).exists():
                continue
            inv = Invoice.objects.create(
                number=row["number"], company_id=row["company_id"], subscription=sub, plan=plan_map[row["plan_id"]], plan_name=row["plan_name"],
                interval=row["interval"], kind=row["kind"], period_start=_dt(row["period_start"]), period_end=_dt(row["period_end"]),
                subtotal=_dec(row["subtotal"]), tax_amount=_dec(row["tax_amount"]), total=_dec(row["total"]), currency=row["currency"],
                status=row["status"], issued_at=_dt(row["issued_at"]), due_at=_dt(row["due_at"]), paid_at=_dt(row["paid_at"]), note=row["note"])
            inv_map[row["id"]] = inv
            counts["invoices"] += 1
        for row in data.get("payment", []):
            inv = inv_map.get(row["invoice_id"])
            if inv is None:
                continue
            Payment.objects.create(
                invoice=inv, company_id=row["company_id"], amount=_dec(row["amount"]), method=row["method"], status=row["status"],
                reference=row["reference"], proof=row["proof"] or None, note=row["note"], stripe_session_id=row["stripe_session_id"],
                stripe_payment_intent=row["stripe_payment_intent"], submitted_by_name=users.get(str(row.get("submitted_by_id")), ""),
                reviewed_at=_dt(row["reviewed_at"]), review_note=row["review_note"])
            counts["payments"] += 1
        for row in data.get("billingevent", []):
            BillingEvent.objects.create(
                company_id=row["company_id"], company_name=(companies.get(str(row["company_id"]), {}).get("name") or "")[:200], kind=row["kind"],
                message=row["message"], actor=users.get(str(row.get("actor_id")), ""), created_at=_dt(row["created_at"]) or datetime.now())
            counts["events"] += 1
    return counts


class Command(BaseCommand):
    help = ("Sets up the owner console database (owner.sqlite3): creates / updates its tables and imports data saved from the old billing tables. "
            "Safe to run again.   python manage.py setup_owner [--email you@example.com --name \"Your Name\"]")

    def add_arguments(self, parser):
        parser.add_argument("--email", default="", help="Also create the first owner account with this e-mail (you are asked for a password).")
        parser.add_argument("--name", default="")
        parser.add_argument("--password", default="")
        parser.add_argument("--seed-plans", action="store_true", help="Create the Starter / Business / Enterprise plans if there are none.")

    def handle(self, *args, **options):
        alias = db.alias()
        self.stdout.write(f"Owner database: {settings.DATABASES[alias]['NAME']} (alias '{alias}')")
        call_command("migrate", "ownerconsole", database=alias, verbosity=0)
        self.stdout.write(self.style.SUCCESS("Owner tables are ready."))
        PlatformSettings.get()

        export = Path(settings.BASE_DIR) / EXPORT
        if export.exists():
            counts = import_legacy(export)
            export.rename(export.with_suffix(".json.imported"))
            self.stdout.write(self.style.SUCCESS("Imported from the old billing tables: " + ", ".join(f"{v} {k}" for k, v in counts.items())))
        if options["seed_plans"] and not Plan.objects.exists():
            call_command("seed_plans")
        if options["email"]:
            if OwnerAccount.objects.filter(email=options["email"].strip().lower()).exists():
                raise CommandError("That owner account already exists. Use create_owner to reset its password.")
            args = [options["email"]]
            kwargs = {"name": options["name"], "password": options["password"]}
            call_command("create_owner", *args, **kwargs)
        elif not OwnerAccount.objects.exists():
            self.stdout.write(self.style.WARNING("No owner account yet. Create one:  python manage.py create_owner you@example.com --name \"Your Name\""))
        self.stdout.write("Start the console:  python manage.py runserver 8001 --settings=config.owner_settings")
