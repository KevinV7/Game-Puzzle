"""The score rule, defined once and computed in the database.

Nothing stores a score: every reader (profile, leaderboard, win panel) annotates
``POINTS`` onto won attempts. ``game`` never imports ``scoring``; ``scoring``
imports this module.
"""

from django.db.models import ExpressionWrapper, F, IntegerField

from .models import Game

# The operands mix PositiveIntegerField and PositiveSmallIntegerField, so the
# output type must be stated.
POINTS = ExpressionWrapper(
    (F("seconds_remaining") + F("moves_remaining")) * F("level__multiplier"),
    output_field=IntegerField(),
)


def won_with_points():
    """Won attempts only, each annotated with ``points``."""
    return Game.objects.filter(status=Game.WON).annotate(points=POINTS)


def score_breakdown(game):
    """The score of one attempt as the win panel shows it, or None unless it was won.

    One query on the same ``POINTS`` annotation every other reader uses, so the
    panel can never disagree with the profile or the leaderboard. Nothing is stored.
    """
    if game.status != Game.WON:
        return None
    return (
        won_with_points()
        .filter(pk=game.pk)
        .values(
            "seconds_remaining",
            "moves_remaining",
            multiplier=F("level__multiplier"),
            points=POINTS,
        )
        .get()
    )
