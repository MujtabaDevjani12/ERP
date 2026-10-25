"""
manage.py promote_super_admin <username> [--create --password ...] [--demote]

Gives a user the "Super Admin" role: full access to every screen and action.
Handy right after install, or if the only admin got locked out.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from rbac.backends import SUPER_ADMIN_ROLE


class Command(BaseCommand):
    help = "Make a user a Super Admin (full access), optionally creating the account."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--create", action="store_true", help="Create the user if it does not exist.")
        parser.add_argument("--password", help="Password for a new / existing user (asked for with --create when omitted).")
        parser.add_argument("--email", default="")
        parser.add_argument("--demote", action="store_true", help="Remove the Super Admin role instead.")

    def handle(self, *args, **opts):
        User = get_user_model()
        username = opts["username"]
        user = User.objects.filter(**{User.USERNAME_FIELD: username}).first()
        if user is None:
            if not opts["create"]:
                raise CommandError(f'No user "{username}". Add --create --password ... to make one.')
            password = opts.get("password")
            if not password:
                import getpass
                password = getpass.getpass("Password: ")
            user = User.objects.create_user(username, email=opts["email"], password=password)
            self.stdout.write(f'Created user "{username}".')
        elif opts.get("password"):
            user.set_password(opts["password"])
            user.save(update_fields=["password"])

        # make sure the role exists with every permission (normally done at the end of `migrate`)
        from rbac.signals import sync_super_admin_role
        from django.apps import apps
        configs = [c for c in apps.get_app_configs() if c.models_module is not None]
        sync_super_admin_role(sender=configs[-1])
        role = Group.objects.get(name=SUPER_ADMIN_ROLE)

        if opts["demote"]:
            others = User.objects.filter(is_active=True, is_superuser=True).exclude(pk=user.pk)
            if not others.exists():
                raise CommandError("That is the only active Super Admin - promote someone else first.")
            user.groups.remove(role)
            self.stdout.write(self.style.SUCCESS(f'"{username}" is no longer a Super Admin.'))
            return

        user.is_active = True
        user.save(update_fields=["is_active"])
        user.groups.add(role)                 # rbac.signals also sets is_superuser + is_staff
        self.stdout.write(self.style.SUCCESS(f'"{username}" is now a Super Admin with access to everything.'))
