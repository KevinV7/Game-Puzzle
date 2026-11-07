from django.urls import path

from . import views

urlpatterns = [
    path("registro/", views.signup, name="signup"),
    path("entrar/", views.login_view, name="login"),
    path("salir/", views.logout_view, name="logout"),
]
