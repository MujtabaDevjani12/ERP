from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase

from rbac.permissions import require
from rbac.roles import ROLES


class RolesTests(TestCase):
    def test_seed_roles_creates_all_21_roles_and_is_rerunnable(self):
        call_command("seed_roles", verbosity=0)
        call_command("seed_roles", verbosity=0)
        self.assertEqual(Group.objects.filter(name__in=ROLES.keys()).count(), 21)

    def test_purchase_officer_cannot_approve_or_post(self):
        call_command("seed_roles", verbosity=0)
        user = get_user_model().objects.create_user("po", "po@example.com", "pw-12345-x")
        user.groups.add(Group.objects.get(name="Purchase Officer"))
        user = get_user_model().objects.get(pk=user.pk)  # fresh permission cache
        self.assertTrue(user.has_perm("procurement.add_purchaserequisition"))
        self.assertFalse(user.has_perm("rbac.approve_document"))
        self.assertFalse(user.has_perm("rbac.post_document"))
        self.assertFalse(user.has_perm("procurement.add_purchaseinvoice"))

    def test_require_helper(self):
        user = get_user_model().objects.create_user("plain", "p@example.com", "pw-12345-x")
        with self.assertRaises(PermissionDenied):
            require(user, "post_document")
        require(None, "post_document")  # system action is allowed
        admin = get_user_model().objects.create_superuser("root", "r@example.com", "pw-12345-x")
        require(admin, "post_document")
