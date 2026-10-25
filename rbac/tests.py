from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase

from rbac.permissions import require
from rbac.roles import ROLES


class RolesTests(TestCase):
    def test_seed_roles_creates_all_23_roles_and_is_rerunnable(self):
        call_command("seed_roles", verbosity=0)
        call_command("seed_roles", verbosity=0)
        self.assertEqual(Group.objects.filter(name__in=ROLES.keys()).count(), 23)

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


# ---------------------------------------------------------------------------------------------- Super Admin & role screens
from django.contrib.auth.models import Permission  # noqa: E402
from django.urls import reverse  # noqa: E402

from company.models import Company  # noqa: E402
from rbac.backends import SUPER_ADMIN_ROLE, is_super_admin  # noqa: E402
from tenants.models import Store  # noqa: E402


class SuperAdminTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        call_command("seed_roles", verbosity=0)

    def test_super_admin_role_holds_every_permission(self):
        role = Group.objects.get(name=SUPER_ADMIN_ROLE)
        self.assertEqual(role.permissions.count(), Permission.objects.count())

    def test_member_of_super_admin_role_passes_every_check_and_gets_flags(self):
        user = self.User.objects.create_user("owner", "o@example.com", "pw-12345-x")
        self.assertFalse(user.has_perm("accounting.add_journalentry"))
        user.groups.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        user = self.User.objects.get(pk=user.pk)
        self.assertTrue(user.is_superuser and user.is_staff)
        self.assertTrue(user.has_perm("accounting.add_journalentry"))
        self.assertTrue(user.has_perm("rbac.post_document"))
        self.assertTrue(is_super_admin(user))

    def test_removing_the_role_removes_superuser_flag(self):
        user = self.User.objects.create_user("owner", "o@example.com", "pw-12345-x")
        role = Group.objects.get(name=SUPER_ADMIN_ROLE)
        user.groups.add(role)
        user.groups.remove(role)
        user = self.User.objects.get(pk=user.pk)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_perm("accounting.add_journalentry"))

    def test_seed_roles_puts_existing_superusers_in_the_role(self):
        root = self.User.objects.create_superuser("root", "r@example.com", "pw-12345-x")
        call_command("seed_roles", verbosity=0)
        self.assertTrue(root.groups.filter(name=SUPER_ADMIN_ROLE).exists())

    def test_inactive_super_admin_has_nothing(self):
        user = self.User.objects.create_user("gone", "g@example.com", "pw-12345-x")
        user.groups.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        self.User.objects.filter(pk=user.pk).update(is_active=False)
        user = self.User.objects.get(pk=user.pk)
        self.assertFalse(user.has_perm("accounting.add_journalentry"))

    def test_promote_command_creates_and_promotes(self):
        call_command("promote_super_admin", "boss", "--create", "--password", "pw-12345-x", verbosity=0)
        boss = self.User.objects.get(username="boss")
        self.assertTrue(boss.is_superuser and boss.groups.filter(name=SUPER_ADMIN_ROLE).exists())

    def test_auditor_is_read_only(self):
        user = self.User.objects.create_user("aud", "a@example.com", "pw-12345-x")
        user.groups.add(Group.objects.get(name="Auditor"))
        user = self.User.objects.get(pk=user.pk)
        self.assertTrue(user.has_perm("accounting.view_journalentry"))
        self.assertFalse(user.has_perm("accounting.add_journalentry"))
        self.assertFalse(user.has_perm("rbac.approve_document"))


class RoleAndUserScreenTests(TestCase):
    def setUp(self):
        self.User = get_user_model()
        call_command("seed_roles", verbosity=0)
        company = Company.objects.create(name="Acme")
        Store.objects.create(name="Main", company=company)
        self.owner = self.User.objects.create_user("owner", "o@example.com", "pw-12345-x")
        self.owner.groups.add(Group.objects.get(name=SUPER_ADMIN_ROLE))
        # an HR-ish manager who may edit roles & users but holds only a few other permissions
        self.mgr_role = Group.objects.create(name="Role Manager")
        self.mgr_role.permissions.set(Permission.objects.filter(
            codename__in=["view_group", "add_group", "change_group", "view_user", "add_user", "change_user", "view_employee"],
        ))
        self.mgr = self.User.objects.create_user("mgr", "m@example.com", "pw-12345-x", is_staff=True)
        self.mgr.groups.add(self.mgr_role)

    def _perm(self, app, codename):
        return Permission.objects.get(content_type__app_label=app, codename=codename)

    def test_role_form_renders_permission_matrix(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("accounts:role_add"))
        self.assertContains(resp, "Permissions")
        self.assertContains(resp, 'name="permissions"')
        self.assertContains(resp, "Employees")

    def test_super_admin_role_is_read_only(self):
        self.client.force_login(self.owner)
        role = Group.objects.get(name=SUPER_ADMIN_ROLE)
        before = role.permissions.count()
        self.client.post(reverse("accounts:role_edit", args=[role.pk]), {"name": "Hacked", "permissions": []})
        role.refresh_from_db()
        self.assertEqual(role.name, SUPER_ADMIN_ROLE)
        self.assertEqual(role.permissions.count(), before)
        self.client.post(reverse("accounts:role_delete", args=[role.pk]))
        self.assertTrue(Group.objects.filter(name=SUPER_ADMIN_ROLE).exists())

    def test_role_editor_cannot_grant_permissions_they_do_not_hold(self):
        self.client.force_login(self.mgr)
        allowed = self._perm("hr", "view_employee")
        forbidden = self._perm("accounting", "delete_journalentry")
        resp = self.client.post(reverse("accounts:role_add"), {"name": "Sneaky", "permissions": [allowed.pk, forbidden.pk]})
        self.assertEqual(resp.status_code, 302)
        perms = set(Group.objects.get(name="Sneaky").permissions.values_list("pk", flat=True))
        self.assertIn(allowed.pk, perms)
        self.assertNotIn(forbidden.pk, perms)

    def test_user_without_group_permissions_is_blocked(self):
        plain = self.User.objects.create_user("plain", "p@example.com", "pw-12345-x", is_staff=True)
        self.client.force_login(plain)
        self.assertEqual(self.client.get(reverse("accounts:role_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:role_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:staff_user_add")).status_code, 403)

    def test_view_only_role_user_cannot_add_or_edit(self):
        viewer = self.User.objects.create_user("viewer", "v@example.com", "pw-12345-x", is_staff=True)
        role = Group.objects.create(name="Viewer")
        role.permissions.set(Permission.objects.filter(codename__in=["view_group", "view_user"]))
        viewer.groups.add(role)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("accounts:role_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("accounts:role_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:staff_user_add")).status_code, 403)
        self.assertEqual(self.client.get(reverse("accounts:staff_user_edit", args=[self.mgr.pk])).status_code, 403)

    def test_manager_cannot_create_super_admin_or_edit_one(self):
        self.client.force_login(self.mgr)
        resp = self.client.get(reverse("accounts:staff_user_add"))
        self.assertNotContains(resp, 'name="is_super_admin"')
        self.assertEqual(self.client.get(reverse("accounts:staff_user_edit", args=[self.owner.pk])).status_code, 403)
        data = {"username": "newbie", "password": "pw-12345-Xyz", "is_active": "on", "is_super_admin": "on"}
        self.client.post(reverse("accounts:staff_user_add"), data)
        new = self.User.objects.get(username="newbie")
        self.assertFalse(new.is_superuser)

    def test_manager_can_only_hand_out_roles_within_their_own_access(self):
        self.client.force_login(self.mgr)
        resp = self.client.get(reverse("accounts:staff_user_add"))
        self.assertContains(resp, "Role Manager")                 # subset of own permissions
        self.assertNotContains(resp, "Finance Manager")           # more than they hold
        self.assertNotContains(resp, ">Super Admin<")

    def test_owner_creates_super_admin_via_form(self):
        self.client.force_login(self.owner)
        data = {"username": "cfo", "password": "pw-12345-Xyz", "is_active": "on", "is_super_admin": "on"}
        resp = self.client.post(reverse("accounts:staff_user_add"), data)
        self.assertEqual(resp.status_code, 302)
        cfo = self.User.objects.get(username="cfo")
        self.assertTrue(cfo.is_superuser and cfo.is_staff and cfo.has_perm("accounting.delete_journalentry"))

    def test_new_user_needs_a_password(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("accounts:staff_user_add"), {"username": "nopass", "is_active": "on"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.User.objects.filter(username="nopass").exists())

    def test_last_super_admin_is_protected(self):
        self.client.force_login(self.owner)
        data = {"username": "owner", "is_active": "on"}                      # un-ticks Super Admin
        resp = self.client.post(reverse("accounts:staff_user_edit", args=[self.owner.pk]), data)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(is_super_admin(self.User.objects.get(pk=self.owner.pk)))
        self.client.post(reverse("accounts:staff_user_delete", args=[self.owner.pk]))
        self.assertTrue(self.User.objects.filter(pk=self.owner.pk).exists())

    def test_sidebar_hides_what_the_role_cannot_open(self):
        cashier = self.User.objects.create_user("cash", "c@example.com", "pw-12345-x")
        role = Group.objects.create(name="Catalog Viewer")
        role.permissions.set(Permission.objects.filter(codename="view_product"))
        cashier.groups.add(role)
        self.client.force_login(cashier)
        resp = self.client.get(reverse("catalog:product_list"))
        self.assertContains(resp, reverse("catalog:product_list"))
        self.assertNotContains(resp, reverse("accounts:staff_user_list"))
        self.assertNotContains(resp, reverse("accounts:role_list"))
        self.assertNotContains(resp, reverse("suppliers:purchase_add"))
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("catalog:product_list"))
        self.assertContains(resp, reverse("accounts:role_list"))
