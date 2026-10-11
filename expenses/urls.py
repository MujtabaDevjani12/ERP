from django.urls import path

from . import pos_views, views

app_name = "expenses"

urlpatterns = [
    path("", views.ExpenseListView.as_view(), name="expense_list"),
    path("add/", views.ExpenseCreateView.as_view(), name="expense_add"),
    path("<int:pk>/edit/", views.ExpenseUpdateView.as_view(), name="expense_edit"),
    path("<int:pk>/delete/", views.ExpenseDeleteView.as_view(), name="expense_delete"),

    path("pos/", pos_views.pos_expense, name="pos_expense"),

    path("categories/", views.ExpenseCategoryListView.as_view(), name="category_list"),
    path("categories/add/", views.ExpenseCategoryCreateView.as_view(), name="category_add"),
    path("categories/<int:pk>/edit/", views.ExpenseCategoryUpdateView.as_view(), name="category_edit"),
    path("categories/<int:pk>/delete/", views.ExpenseCategoryDeleteView.as_view(), name="category_delete"),
]
