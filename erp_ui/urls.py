from django.urls import path

from . import search, views

app_name = "erp_ui"

urlpatterns = [
    path("search/", search.search, name="search"),
    path("lookup/<slug:app_label>/<slug:model_name>/", views.lookup, name="lookup"),
    path("m/<slug:app_label>/", views.module_home, name="module"),
    path("m/<slug:app_label>/<slug:model_name>/", views.model_list, name="list"),
    path("m/<slug:app_label>/<slug:model_name>/add/", views.model_edit, name="add"),
    path("m/<slug:app_label>/<slug:model_name>/<str:pk>/", views.model_edit, name="edit"),
    path("m/<slug:app_label>/<slug:model_name>/<str:pk>/preview/", views.model_preview, name="preview"),
    path("m/<slug:app_label>/<slug:model_name>/<str:pk>/action/", views.object_action, name="action"),
    path("m/<slug:app_label>/<slug:model_name>/<str:pk>/delete/", views.model_delete, name="delete"),
]
