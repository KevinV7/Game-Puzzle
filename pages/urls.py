from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("instrucciones/", views.instructions, name="instructions"),
    path("jugar/", views.play, name="play"),
    path("clasificacion/", views.leaderboard, name="leaderboard"),
]
