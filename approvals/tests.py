from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from approvals import services as ap
from approvals.models import ApprovalLevel, ApprovalRequest, ApprovalWorkflow
from company.models import Company
from core.errors import ApprovalError
from tenants.models import Store


class ApprovalEngineTests(TestCase):
    """Uses a Store as the 'document' since it has a status-like field we can
    emulate with `phone` (any saved model works for the engine itself)."""

    def setUp(self):
        User = get_user_model()
        self.company = Company.objects.create(name="Acme")
        self.doc = Store.objects.create(name="Doc", company=self.company)
        self.maker = User.objects.create_user("maker", "m@example.com", "pw-12345-x")
        self.l1 = User.objects.create_user("l1", "a@example.com", "pw-12345-x")
        self.l2 = User.objects.create_user("l2", "b@example.com", "pw-12345-x")
        self.group = Group.objects.create(name="Level2Group")
        self.l2.groups.add(self.group)
        wf = ApprovalWorkflow.objects.create(company=self.company, name="PO", document_type="purchase_order", min_amount=0)
        ApprovalLevel.objects.create(workflow=wf, level=1, approver_user=self.l1)
        ApprovalLevel.objects.create(workflow=wf, level=2, approver_group=self.group)
        self.wf = wf
        self.events = []
        ap.register_handler(
            "purchase_order",
            on_approved=lambda r: self.events.append("approved"),
            on_rejected=lambda r: self.events.append("rejected"),
            on_returned=lambda r: self.events.append("returned"),
        )

    def _submit(self, amount=1000):
        return ap.submit(self.doc, "purchase_order", company=self.company, user=self.maker, amount=amount, number="PO-1")

    def test_two_level_flow_in_order(self):
        req = self._submit()
        self.assertEqual(req.status, "pending")
        self.assertEqual(req.current_level, 1)
        with self.assertRaises(ApprovalError):
            ap.approve(req, self.l2)             # level 2 user cannot skip level 1
        ap.approve(req, self.l1, "ok")
        req.refresh_from_db()
        self.assertEqual((req.status, req.current_level), ("pending", 2))
        ap.approve(req, self.l2, "ok too")
        req.refresh_from_db()
        self.assertEqual(req.status, "approved")
        self.assertEqual(self.events, ["approved"])
        self.assertGreaterEqual(req.actions.count(), 3)

    def test_reject_requires_comment_and_fires_handler(self):
        req = self._submit()
        with self.assertRaises(ApprovalError):
            ap.reject(req, self.l1, "")
        ap.reject(req, self.l1, "too expensive")
        req.refresh_from_db()
        self.assertEqual(req.status, "rejected")
        self.assertEqual(self.events, ["rejected"])

    def test_return_then_resubmit(self):
        req = self._submit()
        ap.return_for_correction(req, self.l1, "fix quantity")
        req.refresh_from_db()
        self.assertEqual(req.status, "returned")
        ap.resubmit(req, self.maker)
        req.refresh_from_db()
        self.assertEqual((req.status, req.current_level), ("pending", 1))

    def test_delegate_lets_another_user_act(self):
        other = get_user_model().objects.create_user("deputy", "d@example.com", "pw-12345-x")
        req = self._submit()
        with self.assertRaises(ApprovalError):
            ap.approve(req, other)
        ap.delegate(req, self.l1, other, "on leave")
        ap.approve(req, other)
        req.refresh_from_db()
        self.assertEqual(req.current_level, 2)

    def test_cannot_submit_twice_while_pending(self):
        self._submit()
        with self.assertRaises(ApprovalError):
            self._submit()

    def test_submitter_cannot_approve_own_document(self):
        wf_user = get_user_model().objects.create_user("selfie", "s@example.com", "pw-12345-x")
        level = self.wf.levels.get(level=1)
        level.approver_user = wf_user
        level.save()
        req = ap.submit(self.doc, "purchase_order", company=self.company, user=wf_user, amount=1, number="PO-2")
        with self.assertRaises(ApprovalError):
            ap.approve(req, wf_user)

    def test_no_workflow_means_auto_approved(self):
        req = ap.submit(self.doc, "unconfigured_type", company=self.company, user=self.maker, amount=5)
        self.assertEqual(req.status, "approved")

    def test_amount_threshold_picks_workflow(self):
        big = ApprovalWorkflow.objects.create(company=self.company, name="Big PO", document_type="purchase_order", min_amount=100000)
        ApprovalLevel.objects.create(workflow=big, level=1, approver_user=self.l1)
        self.assertEqual(ap.find_workflow(self.company, "purchase_order", 5000), self.wf)
        self.assertEqual(ap.find_workflow(self.company, "purchase_order", 250000), big)
