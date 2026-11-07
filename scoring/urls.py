from django.urls import path

from . import views

urlpatterns = [
    path("perfil/", views.profile, name="profile"),
    path("clasificacion/", views.leaderboard, name="leaderboard"),
]
