"""Factories for the scoring tests. Time is always explicit."""

from datetime import timedelta

from game.models import Game
from game.tests.helpers import T0, at, make_game, make_lost, make_user

__all__ = ["T0", "at", "make_user", "make_game", "make_lost", "make_win"]


def make_win(user, slug="facil", *, seconds_remaining, moves_remaining, ended_at=None, seconds=60):
    """Insert a won Game with explicit remaining fields.

    ``ended_at`` defaults to ``seconds`` after T0 and the attempt started
    ``seconds`` before it ended, so the duration is exactly ``seconds``.
    """
    ended_at = at(seconds) if ended_at is None else ended_at
    started_at = ended_at - timedelta(seconds=seconds)
    return make_game(
        user,
        slug,
        started_at=started_at,
        status=Game.WON,
        ended_at=ended_at,
        seconds_remaining=seconds_remaining,
        moves_remaining=moves_remaining,
    )
