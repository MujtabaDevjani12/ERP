from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from company.models import Company
from tenants.models import Store

from . import services
from .models import Notification


class NotificationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Acme")
        call_command("seed_chart_of_accounts", company="Acme", verbosity=0)
        self.store = Store.objects.create(name="Main", company=self.company)
        self.admin = get_user_model().objects.create_superuser("boss", "b@example.com", "dev-only-pw-123", store=self.store)

    def test_same_key_is_sent_once_per_user(self):
        self.assertEqual(services.notify_users(self.company, [self.admin], "Hello", key="k1"), 1)
        self.assertEqual(services.notify_users(self.company, [self.admin], "Hello", key="k1"), 0)
        self.assertEqual(Notification.objects.count(), 1)

    def test_generate_alerts_runs_on_empty_company_and_is_repeatable(self):
        first = services.generate_alerts(self.company)
        self.assertIn("low_stock", first)
        second = services.generate_alerts(self.company)
        self.assertEqual(sum(second.values()), 0)

    def test_inbox_and_mark_read(self):
        services.notify_users(self.company, [self.admin], "Hello", key="k1")
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("notifications:inbox")).status_code, 200)
        self.client.post(reverse("notifications:read_all"))
        self.assertEqual(services.unread_count(self.admin), 0)

    def test_users_see_only_their_own(self):
        other = get_user_model().objects.create_user("o", password="dev-only-pw-123", store=self.store)
        services.notify_users(self.company, [self.admin], "Secret", key="k2")
        self.client.force_login(other)
        self.assertNotContains(self.client.get(reverse("notifications:inbox")), "Secret")
