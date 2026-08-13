from django.urls import path

from web import views

app_name = "web"

urlpatterns = [
    path("", views.home, name="home"),
    path("modelo/", views.model_detail, name="model"),
    path("veiculo/", views.detail, name="detail"),
    path("comparar/", views.compare, name="compare"),
]
