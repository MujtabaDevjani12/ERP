"""New list filters: stock by category, customers by group."""
from django.test import TestCase
from django.urls import reverse

from catalog.models import Category
from core.pos_fixtures import PASSWORD, PosWorld
from customers.models import Customer
from pricing.models import CustomerGroup


class ListFilterTests(TestCase):
    def setUp(self):
        self.w = PosWorld()
        self.client.login(username="manager", password=PASSWORD)

    def test_stock_list_category_filter(self):
        cat = Category.objects.create(store=self.w.store, name="Drinks")
        self.w.tea.product.category = cat
        self.w.tea.product.save()
        url = reverse("inventory:stock_list")
        names = lambda r: {s.variant.product.name for s in r.context["stock_items"]}
        self.assertIn(self.w.tea.product.name, names(self.client.get(url, {"category": cat.pk})))
        self.assertNotIn(self.w.milk.product.name, names(self.client.get(url, {"category": cat.pk})))
        self.assertContains(self.client.get(url), 'name="category"')

    def test_customer_group_filter(self):
        group = CustomerGroup.objects.create(store=self.w.store, name="Wholesale")
        Customer.objects.create(store=self.w.store, name="Bulk Buyer", group=group)
        Customer.objects.create(store=self.w.store, name="Plain Buyer")
        page = self.client.get(reverse("customers:customer_list"), {"group": group.pk})
        self.assertContains(page, "Bulk Buyer")
        self.assertNotContains(page, "Plain Buyer")
