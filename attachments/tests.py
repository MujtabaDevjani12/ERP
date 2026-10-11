import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from attachments.services import attach, attachments_for
from company.models import Company

_MEDIA = tempfile.mkdtemp(prefix="erp-test-media-")


@override_settings(MEDIA_ROOT=_MEDIA)
class AttachmentTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_MEDIA, ignore_errors=True)

    def setUp(self):
        self.company = Company.objects.create(name="Acme")

    def test_valid_pdf_is_attached_and_listed(self):
        f = SimpleUploadedFile("contract.pdf", b"%PDF-1.4 fake body", content_type="application/pdf")
        att = attach(self.company, f, category="vendor_document")
        self.assertEqual(att.original_name, "contract.pdf")
        self.assertEqual(list(attachments_for(self.company)), [att])

    def test_disallowed_extension_rejected(self):
        f = SimpleUploadedFile("run.exe", b"MZ....", content_type="application/octet-stream")
        with self.assertRaises(ValidationError):
            attach(self.company, f)

    def test_content_must_match_extension(self):
        f = SimpleUploadedFile("fake.pdf", b"not really a pdf", content_type="application/pdf")
        with self.assertRaises(ValidationError):
            attach(self.company, f)

    @override_settings(ATTACHMENT_MAX_MB=1)
    def test_size_limit_enforced(self):
        f = SimpleUploadedFile("big.pdf", b"%PDF" + b"0" * (1024 * 1024 + 10), content_type="application/pdf")
        with self.assertRaises(ValidationError):
            attach(self.company, f)

    def test_cannot_attach_to_unsaved_object(self):
        f = SimpleUploadedFile("a.pdf", b"%PDF-1.4", content_type="application/pdf")
        with self.assertRaises(ValueError):
            attach(Company(name="Unsaved"), f)
