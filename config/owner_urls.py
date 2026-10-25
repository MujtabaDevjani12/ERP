"""URLconf of the separate owner site (run with config.owner_settings). Nothing of the ERP is reachable here."""
from django.urls import include, path

urlpatterns = [
    path("", include("ownerconsole.urls")),
]
