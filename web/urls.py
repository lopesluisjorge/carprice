from django.urls import path

from web import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("veiculo/", views.detail, name="detail"),
    path("comparar/", views.compare, name="compare"),
    # HTMX fragments for the brand -> model -> year cascade.
    path("fragmentos/modelos/", views.model_options, name="model_options"),
    path("fragmentos/anos/", views.year_options, name="year_options"),
]
