"""Data backup & restore: the engine (core.backup), the ERP Settings > Data backup page and the owner console's backup section."""
import io
import json
import shutil
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from catalog.models import Product, ProductVariant, Unit
from company.models import Company
from core import backup
from core.pos_fixtures import PASSWORD, PosWorld
from inventory.models import StockItem
from tenants.models import Store, Warehouse


def other_company():
    """A second company with its own branch and product - it must never leak into the first one's backup."""
    company = Company.objects.create(name="Rival Ltd")
    store = Store.objects.create(name="Rival Main", company=company)
    Warehouse.objects.create(store=store, name="Rival floor", is_default=True)
    unit = Unit.objects.create(store=store, name="Piece", short_name="pc")
    product = Product(store=store, unit=unit, name="Rival Tea", sku="RT")
    product._skip_default_variant = True
    product.save()
    ProductVariant.objects.create(product=product, sku="RT-1", selling_price=Decimal("9"), cost_price=Decimal("5"))
    return company


class BackupBase(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        override = override_settings(BACKUP_DIR=Path(self.tmp) / "backups", MEDIA_ROOT=Path(self.tmp) / "media")
        override.enable()
        self.addCleanup(override.disable)
        self.world = PosWorld()
        self.rival = other_company()

    def names(self, company):
        return sorted(Product.objects.filter(store__company=company).values_list("name", flat=True))


class EngineTests(BackupBase):
    def test_scope_holds_only_this_company(self):
        scope = backup.company_scope(self.world.company)
        self.assertEqual(scope[Company], {self.world.company.pk})
        self.assertEqual(scope[Product], set(Product.objects.filter(store__company=self.world.company).values_list("pk", flat=True)))
        rival_products = set(Product.objects.filter(store__company=self.rival).values_list("pk", flat=True))
        self.assertFalse(scope[Product] & rival_products)
        self.assertTrue(all(m._meta.app_label not in backup.EXCLUDED_APPS for m in scope))

    def test_round_trip_restores_deleted_and_changed_rows(self):
        data, manifest = backup.company_backup(self.world.company)
        before = self.names(self.world.company)
        stock_before = StockItem.objects.filter(variant__product__store__company=self.world.company).count()
        Product.objects.filter(name="Milk").delete()
        Product.objects.filter(name="Tea").update(name="Renamed")
        Product.objects.create(store=self.world.store, unit=self.world.unit, name="Added later", sku="ADD")
        backup.restore_company(self.world.company, io.BytesIO(data))
        self.assertEqual(self.names(self.world.company), before)
        self.assertEqual(StockItem.objects.filter(variant__product__store__company=self.world.company).count(), stock_before)
        self.assertEqual(self.names(self.rival), ["Rival Tea"])                  # the other company was not touched
        self.assertEqual(ProductVariant.objects.filter(product__store__company=self.world.company).count(),
                         len(json.loads(zipfile.ZipFile(io.BytesIO(data)).read("data/catalog.productvariant.json"))))

    def test_restore_creates_no_extra_default_variants(self):
        data, _ = backup.company_backup(self.world.company)
        count = ProductVariant.objects.count()
        backup.restore_company(self.world.company, io.BytesIO(data))
        self.assertEqual(ProductVariant.objects.count(), count)

    def test_new_rows_after_restore_get_fresh_ids(self):
        data, _ = backup.company_backup(self.world.company)
        backup.restore_company(self.world.company, io.BytesIO(data))
        p = Product.objects.create(store=self.world.store, unit=self.world.unit, name="Fresh", sku="FR")
        self.assertEqual(Product.objects.filter(pk=p.pk).count(), 1)

    def test_other_companys_file_is_refused_and_nothing_changes(self):
        data, _ = backup.company_backup(self.rival)
        before = self.names(self.world.company)
        with self.assertRaises(backup.BackupError):
            backup.restore_company(self.world.company, io.BytesIO(data))
        self.assertEqual(self.names(self.world.company), before)

    def test_garbage_and_wrong_kind_are_refused(self):
        with self.assertRaises(backup.BackupError):
            backup.restore_company(self.world.company, io.BytesIO(b"not a zip"))
        owner_data, _ = backup.owner_backup("default")
        with self.assertRaises(backup.BackupError):
            backup.restore_company(self.world.company, io.BytesIO(owner_data))

    def test_media_files_round_trip(self):
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        name = default_storage.save("products/pic.png", ContentFile(b"PNGDATA"))
        Product.objects.filter(name="Tea").update(image=name) if hasattr(Product, "image") else None
        data, manifest = backup.company_backup(self.world.company)
        if hasattr(Product, "image"):
            self.assertIn(name, manifest["media"])
            default_storage.delete(name)
            backup.restore_company(self.world.company, io.BytesIO(data))
            with default_storage.open(name, "rb") as fh:
                self.assertEqual(fh.read(), b"PNGDATA")

    def test_safety_copy_is_listed(self):
        data, manifest = backup.company_backup(self.world.company)
        path = backup.save_safety_copy(data, manifest)
        listed = backup.saved_copies("company", company_id=self.world.company.pk)
        self.assertEqual([c["name"] for c in listed], [path.name])
        self.assertEqual(backup.saved_copy_path(path.name), path)
        with self.assertRaises(backup.BackupError):
            backup.saved_copy_path("../../etc/passwd")


class ErpPageTests(BackupBase):
    def login(self, user=None):
        self.client.login(username=(user or self.world.manager).username, password=PASSWORD)

    def upload(self, data, **over):
        body = {"backup_file": SimpleUploadedFile("b.zip", data, content_type="application/zip"), "confirm": "RESTORE", "password": PASSWORD}
        body.update(over)
        return self.client.post(reverse("configuration:backup_import"), body, follow=True)

    def test_section_page_renders_and_is_listed(self):
        self.login()
        resp = self.client.get(reverse("configuration:hub_section", args=["backup"]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Download backup")

    def test_download_is_a_zip_of_this_company_only(self):
        self.login()
        resp = self.client.get(reverse("configuration:backup_download"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/zip")
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        manifest = json.loads(zf.read("manifest.json"))
        self.assertEqual(manifest["company_id"], self.world.company.pk)
        self.assertNotIn("Rival", zf.read("data/catalog.product.json").decode())

    def test_cashier_cannot_download_or_restore(self):
        self.login(self.world.cashier)
        self.assertEqual(self.client.get(reverse("configuration:backup_download")).status_code, 403)
        self.assertEqual(self.client.post(reverse("configuration:backup_import"), {}).status_code, 403)

    def test_restore_needs_password_and_confirm_word(self):
        self.login()
        data, _ = backup.company_backup(self.world.company)
        Product.objects.filter(name="Milk").delete()
        self.upload(data, password="wrong")
        self.assertNotIn("Milk", self.names(self.world.company))
        self.upload(data, confirm="no")
        self.assertNotIn("Milk", self.names(self.world.company))
        resp = self.upload(data)
        self.assertIn("Milk", self.names(self.world.company))
        self.assertContains(resp, "Backup restored")
        self.assertEqual(len(backup.saved_copies("company", company_id=self.world.company.pk)), 1)   # the safety copy of the data as it was before

    def test_restore_rejects_bad_file(self):
        self.login()
        before = self.names(self.world.company)
        resp = self.upload(b"junk")
        self.assertContains(resp, "not a backup file")
        self.assertEqual(self.names(self.world.company), before)
        self.assertEqual(backup.saved_copies("company", company_id=self.world.company.pk), [])

    def test_safety_copy_download_only_for_own_company(self):
        self.login()
        data, manifest = backup.company_backup(self.rival)
        theirs = backup.save_safety_copy(data, manifest)
        refused = self.client.get(reverse("configuration:backup_saved", args=[theirs.name]))
        self.assertEqual(refused.status_code, 302)                                       # not handed over: back to the page with a message
        mine_data, mine_manifest = backup.company_backup(self.world.company)
        mine = backup.save_safety_copy(mine_data, mine_manifest)
        self.assertEqual(self.client.get(reverse("configuration:backup_saved", args=[mine.name])).status_code, 200)

    def test_download_keeps_a_copy_in_the_history_with_date_and_duration(self):
        self.login()
        resp = self.client.get(reverse("configuration:backup_download"))
        names = [c["name"] for c in backup.saved_copies("company", company_id=self.world.company.pk)]
        self.assertEqual(len(names), 1)
        self.assertIn(names[0], resp["Content-Disposition"])
        page = self.client.get(reverse("configuration:hub_section", args=["backup"]))
        self.assertContains(page, "Saved backups")
        self.assertContains(page, names[0])
        self.assertContains(page, reverse("configuration:backup_restore_saved", args=[names[0]]))
        copy = backup.saved_copies("company", company_id=self.world.company.pk)[0]
        self.assertIsNotNone(copy["seconds"])
        self.assertGreater(copy["records"], 0)
        self.assertFalse(copy["safety"])

    def test_restore_button_on_a_saved_backup(self):
        self.login()
        self.client.get(reverse("configuration:backup_download"))
        name = backup.saved_copies("company", company_id=self.world.company.pk)[0]["name"]
        Product.objects.filter(name="Milk").delete()
        wrong = self.client.post(reverse("configuration:backup_restore_saved", args=[name]), {"password": "nope"}, follow=True)
        self.assertContains(wrong, "not correct")
        self.assertNotIn("Milk", self.names(self.world.company))
        ok = self.client.post(reverse("configuration:backup_restore_saved", args=[name]), {"password": PASSWORD}, follow=True)
        self.assertContains(ok, "restored")
        self.assertIn("Milk", self.names(self.world.company))
        kinds = [c["safety"] for c in backup.saved_copies("company", company_id=self.world.company.pk)]
        self.assertIn(True, kinds)                                                       # the safety copy made before restoring is in the list

    def test_another_companys_saved_backup_cannot_be_restored_here(self):
        self.login()
        data, manifest = backup.company_backup(self.rival)
        theirs = backup.save_copy(data, manifest)
        before = self.names(self.world.company)
        r = self.client.post(reverse("configuration:backup_restore_saved", args=[theirs.name]), {"password": PASSWORD}, follow=True)
        self.assertContains(r, "does not belong")
        self.assertEqual(self.names(self.world.company), before)
        self.assertEqual(self.names(self.rival), ["Rival Tea"])

    def test_history_is_trimmed_to_the_newest_copies(self):
        data, manifest = backup.company_backup(self.world.company)
        with override_settings():
            old_keep = backup.KEEP
            backup.KEEP = 3
            self.addCleanup(setattr, backup, "KEEP", old_keep)
            for i in range(5):
                path = backup.save_copy(data, dict(manifest, label=f"x{i}"))
        self.assertEqual(len(backup.saved_copies("company", company_id=self.world.company.pk)), 3)


class OwnerBackupTests(TestCase):
    def setUp(self):
        from billing.testkit import make_owner, make_plans, OWNER_PASSWORD
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        override = override_settings(BACKUP_DIR=Path(self.tmp) / "backups")
        override.enable()
        self.addCleanup(override.disable)
        self.starter, self.pro = make_plans()
        self.boss = make_owner()
        self.password = OWNER_PASSWORD

    def test_round_trip_and_views(self):
        from ownerconsole.models import OwnerAccount, Plan
        from ownerconsole.tests import OWNER_SITE, sign_in
        with override_settings(**OWNER_SITE):
            self.assertEqual(sign_in(self.client).status_code, 302)
            resp = self.client.get(reverse("owner:backup_download"))
            self.assertEqual(resp.status_code, 200)
            data = resp.content
            manifest = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))
            self.assertEqual(manifest["kind"], "owner")
            Plan.objects.filter(slug="pro").delete()
            Plan.objects.filter(slug="starter").update(name="Changed")

            def post(**over):
                body = {"backup_file": SimpleUploadedFile("o.zip", data), "confirm": "RESTORE", "password": self.password}
                body.update(over)
                return self.client.post(reverse("owner:backup_import"), body, follow=True)

            post(password="nope")
            self.assertFalse(Plan.objects.filter(slug="pro").exists())
            post(confirm="x")
            self.assertFalse(Plan.objects.filter(slug="pro").exists())
            resp = post()
            self.assertContains(resp, "Backup restored")
            self.assertTrue(Plan.objects.filter(slug="pro").exists())
            self.assertEqual(Plan.objects.get(slug="starter").name, "Starter")
            self.assertTrue(OwnerAccount.objects.filter(email="boss@owner.test").exists())
            self.assertEqual(len(backup.saved_copies("owner")), 2)          # the download's own copy + the safety copy made by the restore
            resp = self.client.get(reverse("owner:settings"))
            self.assertContains(resp, "Saved backups")
            name = [c for c in backup.saved_copies("owner") if not c["safety"]][0]["name"]
            self.assertContains(resp, name)
            Plan.objects.filter(slug="pro").delete()
            bad = self.client.post(reverse("owner:backup_restore_saved", args=[name]), {"password": "x"}, follow=True)
            self.assertContains(bad, "not correct")
            self.assertFalse(Plan.objects.filter(slug="pro").exists())
            good = self.client.post(reverse("owner:backup_restore_saved", args=[name]), {"password": self.password}, follow=True)
            self.assertContains(good, "restored")
            self.assertTrue(Plan.objects.filter(slug="pro").exists())

    def test_company_backup_is_refused_by_owner_restore(self):
        from ownerconsole.tests import OWNER_SITE, sign_in
        world = PosWorld()
        data, _ = backup.company_backup(world.company)
        with override_settings(**OWNER_SITE):
            sign_in(self.client)
            resp = self.client.post(reverse("owner:backup_import"), {"backup_file": SimpleUploadedFile("c.zip", data), "confirm": "RESTORE",
                                                                    "password": self.password}, follow=True)
            self.assertContains(resp, "restores &#x27;owner&#x27; backups only")

    def test_settings_save_failure_shows_a_message_not_a_500_page(self):
        from unittest import mock

        from django.db import OperationalError

        from ownerconsole.forms import PlatformSettingsForm
        from ownerconsole.models import PlatformSettings
        from ownerconsole.tests import OWNER_SITE, sign_in
        form = PlatformSettingsForm(instance=PlatformSettings.get())
        data = {}
        for name in form.fields:
            v = form.initial.get(name, "")
            data[name] = "on" if v is True else ("" if v is False or v is None else v)
        data.update(google_client_id="1-a.apps.googleusercontent.com", google_client_secret="GOCSPX-x")
        with override_settings(**OWNER_SITE):
            sign_in(self.client)
            with mock.patch("ownerconsole.models.PlatformSettings.save", side_effect=OperationalError("attempt to write a readonly database")):
                r = self.client.post(reverse("owner:settings"), data, follow=True)
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, "readonly database")
