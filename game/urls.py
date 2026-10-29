from django.urls import path

from . import views

# No namespace: base.html and the tests reverse these names directly.
urlpatterns = [
    path("api/partidas/<int:game_id>/", views.game_state, name="game-state"),
    path("api/partidas/<int:game_id>/mover/", views.game_move, name="game-move"),
]
