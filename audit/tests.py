from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from audit.context import get_context
from audit.middleware import AuditContextMiddleware
from audit.models import AuditLog
from audit.services import diff, log_action, snapshot
from company.models import Company
from tenants.models import Store


class LogActionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("auditor", "a@example.com", "pw-12345-x")
        self.company = Company.objects.create(name="Acme")
        self.store = Store.objects.create(name="Karachi", company=self.company)

    def test_log_action_records_user_object_and_values(self):
        entry = log_action(
            AuditLog.UPDATED, self.store, user=self.user,
            old={"name": "Karachi"}, new={"name": "Karachi 2"}, document_number="X-1",
        )
        self.assertEqual(entry.user_repr, "auditor")
        self.assertEqual(entry.object_id, str(self.store.pk))
        self.assertEqual(entry.old_values, {"name": "Karachi"})
        self.assertEqual(entry.company_id, self.company.id)
        self.assertEqual(entry.document_number, "X-1")

    def test_snapshot_and_diff_only_report_changes(self):
        before = snapshot(self.store, fields=["name", "phone"])
        self.store.name = "Renamed"
        old, new = diff(before, snapshot(self.store, fields=["name", "phone"]))
        self.assertEqual(old, {"name": "Karachi"})
        self.assertEqual(new, {"name": "Renamed"})

    def test_entries_are_immutable(self):
        entry = log_action(AuditLog.CREATED, self.store, user=self.user)
        entry.note = "tampered"
        with self.assertRaises(RuntimeError):
            entry.save()
        with self.assertRaises(RuntimeError):
            entry.delete()
        with self.assertRaises(RuntimeError):
            AuditLog.objects.all().update(note="x")
        with self.assertRaises(RuntimeError):
            AuditLog.objects.all().delete()

    def test_user_repr_survives_user_deletion(self):
        entry = log_action(AuditLog.CREATED, self.store, user=self.user)
        self.user.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.user)
        self.assertEqual(entry.user_repr, "auditor")


class MiddlewareAndSignalTests(TestCase):
    def test_middleware_exposes_ip_and_user_during_request_only(self):
        User = get_user_model()
        user = User.objects.create_user("mw", "m@example.com", "pw-12345-x")
        seen = {}

        def view(request):
            seen.update(get_context())
            from django.http import HttpResponse
            return HttpResponse("ok")

        request = RequestFactory().get("/", REMOTE_ADDR="10.1.2.3", HTTP_USER_AGENT="TestAgent")
        request.user = user
        AuditContextMiddleware(view)(request)
        self.assertEqual(seen["ip_address"], "10.1.2.3")
        self.assertEqual(seen["user"], user)
        self.assertIsNone(get_context()["ip_address"])  # reset after the request

    def test_login_and_failed_login_are_logged(self):
        User = get_user_model()
        User.objects.create_user("signaluser", "s@example.com", "pw-12345-x")
        self.assertTrue(self.client.login(username="signaluser", password="pw-12345-x"))
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.LOGIN, user_repr="signaluser").exists())

        self.client.logout()
        self.assertFalse(self.client.login(username="signaluser", password="wrong"))
        failed = AuditLog.objects.filter(action=AuditLog.LOGIN_FAILED).first()
        self.assertIsNotNone(failed)
        self.assertIn("signaluser", failed.note)
        self.assertNotIn("wrong", failed.note)
