import getpass

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email

from ownerconsole.models import OwnerAccount


class Command(BaseCommand):
    help = ("Creates an owner-console account (or resets the password of an existing one). "
            "These accounts are separate from ERP users.  python manage.py create_owner you@example.com --name \"Your Name\"")

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--name", default="")
        parser.add_argument("--password", default="", help="Leave out to be asked (recommended: it stays out of your shell history).")
        parser.add_argument("--support", action="store_true", help="Create a support account (cannot change plans, prices or settings).")

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError("That does not look like an e-mail address.") from exc
        password = options["password"]
        if not password:
            password = getpass.getpass("Password (at least 10 characters): ")
            if password != getpass.getpass("Repeat password: "):
                raise CommandError("The two passwords do not match.")
        if len(password) < 10:
            raise CommandError("Use at least 10 characters.")
        owner = OwnerAccount.objects.filter(email=email).first()
        created = owner is None
        if created:
            owner = OwnerAccount(email=email, name=options["name"] or email.split("@")[0].title())
        elif options["name"]:
            owner.name = options["name"]
        owner.role = OwnerAccount.SUPPORT if options["support"] else (owner.role if not created else OwnerAccount.ADMIN)
        owner.is_active = True
        owner.failed_attempts, owner.locked_until = 0, None
        owner.set_password(password)
        owner.save()
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} owner account {owner.email} ({owner.get_role_display()})."))
        self.stdout.write("Start the console:  python manage.py runserver 8001 --settings=config.owner_settings   then open http://127.0.0.1:8001/")
