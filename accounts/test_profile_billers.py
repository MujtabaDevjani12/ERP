"""My profile (details, photo, password) and the Billers page."""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from configuration.models import POSConfiguration
from core.pos_fixtures import PASSWORD, PosWorld


class ProfileTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="cashier", password=PASSWORD)          # a plain cashier: the page needs no permission
        self.url = reverse("accounts:profile")

    def test_page_opens_for_anyone_signed_in(self):
        page = self.client.get(self.url)
        self.assertContains(page, "My profile")
        self.assertContains(page, "cashier")
        self.assertContains(page, "Change password")

    def test_signed_out_goes_to_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_details_are_saved(self):
        r = self.client.post(self.url, {"form": "details", "first_name": "Ayesha", "last_name": "Khan", "email": "a@example.com", "phone": "0300 1"})
        self.assertRedirects(r, self.url)
        u = User.objects.get(username="cashier")
        self.assertEqual((u.first_name, u.last_name, u.email, u.phone), ("Ayesha", "Khan", "a@example.com", "0300 1"))

    def test_bad_email_is_refused_with_a_message(self):
        r = self.client.post(self.url, {"form": "details", "first_name": "A", "email": "nope"})
        self.assertContains(r, "valid email")

    def test_password_change_keeps_you_signed_in(self):
        r = self.client.post(self.url, {"form": "password", "old_password": PASSWORD, "new_password1": "Brand-new-pass-77", "new_password2": "Brand-new-pass-77"})
        self.assertRedirects(r, self.url)
        self.assertTrue(User.objects.get(username="cashier").check_password("Brand-new-pass-77"))
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_wrong_current_password_is_refused(self):
        r = self.client.post(self.url, {"form": "password", "old_password": "wrong", "new_password1": "Brand-new-pass-77", "new_password2": "Brand-new-pass-77"})
        self.assertContains(r, "incorrect")
        self.assertTrue(User.objects.get(username="cashier").check_password(PASSWORD))

    def test_user_menu_links_to_the_profile(self):
        self.assertContains(self.client.get(reverse("dashboard:home")), self.url)


class BillerTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.url = reverse("accounts:biller_list")

    def test_cashier_without_permission_cannot_open_it(self):
        self.client.login(username="cashier", password=PASSWORD)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_lists_only_billers_with_their_recent_sales(self):
        self.client.login(username="manager", password=PASSWORD)
        from sales.models import Sale
        Sale.objects.create(store=self.w.store, warehouse=self.w.warehouse, biller=self.w.cashier, reference="S-1", status="completed", total_amount=Decimal("250"))
        page = self.client.get(self.url)
        self.assertContains(page, "cashier2")
        self.assertEqual(sorted(b.username for b in page.context["billers"]), ["cashier", "cashier2"])   # the manager is not a biller
        row = [b for b in page.context["billers"] if b.username == "cashier"][0]
        self.assertEqual((row.n, row.value), (1, Decimal("250")))

    def test_search_and_status_filter(self):
        self.client.login(username="manager", password=PASSWORD)
        User.objects.filter(username="cashier2").update(is_active=False)
        self.assertNotContains(self.client.get(self.url, {"status": "inactive", "q": "cashier2"}), "cashier@")
        self.assertEqual([b.username for b in self.client.get(self.url, {"status": "inactive"}).context["billers"]], ["cashier2"])

    def test_make_default_sets_the_pos_default_biller(self):
        self.client.login(username="manager", password=PASSWORD)
        conf = POSConfiguration.objects.get_or_create(store=self.w.store)[0]
        r = self.client.post(reverse("accounts:biller_default", args=[self.w.cashier2.pk]))
        self.assertRedirects(r, self.url)
        conf.refresh_from_db()
        self.assertEqual(conf.default_biller_id, self.w.cashier2.pk)
        self.assertContains(self.client.get(self.url), "Default")

    def test_default_needs_post_and_an_active_biller(self):
        self.client.login(username="manager", password=PASSWORD)
        self.assertRedirects(self.client.get(reverse("accounts:biller_default", args=[self.w.cashier.pk])), self.url)
        self.assertEqual(self.client.post(reverse("accounts:biller_default", args=[self.w.manager.pk])).status_code, 404)

    def test_new_biller_link_preselects_the_flag(self):
        self.client.login(username="manager", password=PASSWORD)
        page = self.client.get(reverse("accounts:staff_user_add") + "?biller=1")
        self.assertTrue(page.context["form"]["is_biller"].value())

    def test_sidebar_has_a_billers_link_for_staff(self):
        self.client.login(username="manager", password=PASSWORD)
        self.assertContains(self.client.get(reverse("dashboard:home")), self.url)
