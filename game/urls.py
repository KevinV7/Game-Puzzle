from django.urls import path

from . import views

# No namespace: base.html and the tests reverse these names directly. The API routes
# come before the level slug route so that "api" can never be read as a slug.
urlpatterns = [
    path("", views.play, name="play"),
    path("api/partidas/<int:game_id>/", views.game_state, name="game-state"),
    path("api/partidas/<int:game_id>/mover/", views.game_move, name="game-move"),
    path("<slug:slug>/empezar/", views.game_start, name="game-start"),
    path("<slug:slug>/", views.game_board, name="game-board"),
]
