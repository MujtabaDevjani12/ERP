"""
manage.py seed_roles

Creates the 22 ERP role groups and adds their permissions. Additive and
re-runnable. Existing groups keep any permission you added by hand
(use --reset to make a role match roles.py exactly).
"Super Admin" always gets every permission, including those of new models.
Apps that are not installed are skipped (with a note).
"""
from django.apps import apps
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

from rbac.roles import ROLES


class Command(BaseCommand):
    help = "Create the ERP role groups and grant their permissions."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Make each built-in role match roles.py exactly (removes permissions that were added by hand). Custom roles are untouched.")

    def handle(self, *args, **options):
        # Make sure permissions of every installed app exist even if migrate's
        # post-migrate hook has not run for a freshly added app yet.
        from django.contrib.auth.management import create_permissions
        for app_config in apps.get_app_configs():
            create_permissions(app_config, verbosity=0)

        skipped = set()
        with transaction.atomic():
            for role_name, grants in ROLES.items():
                group, _ = Group.objects.get_or_create(name=role_name)
                if grants == "ALL":
                    group.permissions.set(Permission.objects.all())          # Super Admin = literally everything
                    self.stdout.write(f"{role_name}: all {Permission.objects.count()} permissions")
                    continue
                perms = []
                for grant in grants:
                    app_label, actions = grant[0], grant[1]
                    only_models = grant[2] if len(grant) > 2 else None
                    if not self._installed(app_label):
                        skipped.add(app_label)
                        continue
                    perms.extend(self._resolve(app_label, actions, only_models))
                if options.get("reset"):
                    group.permissions.set(perms)
                else:
                    group.permissions.add(*perms)
                self.stdout.write(f"{role_name}: {len(perms)} permissions")

        # Anyone flagged is_superuser belongs to the Super Admin role (and the other way round, see rbac.signals).
        from django.contrib.auth import get_user_model
        super_group = Group.objects.filter(name="Super Admin").first()
        if super_group is not None:
            super_group.user_set.add(*get_user_model().objects.filter(is_superuser=True))

        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped apps that are not installed: {', '.join(sorted(skipped))}"))
        self.stdout.write(self.style.SUCCESS("Roles ready."))

    @staticmethod
    def _installed(app_label):
        try:
            apps.get_app_config(app_label)
            return True
        except LookupError:
            return False

    @staticmethod
    def _resolve(app_label, actions, only_models):
        found = []
        qs = Permission.objects.filter(content_type__app_label=app_label)
        for action in actions:
            if action.startswith("erp:"):
                found.extend(Permission.objects.filter(content_type__app_label="rbac", codename=action[4:]))
                continue
            perm_qs = qs.filter(codename__startswith=f"{action}_")
            if only_models:
                perm_qs = perm_qs.filter(codename__in=[f"{action}_{m}" for m in only_models])
            found.extend(perm_qs)
        return found
