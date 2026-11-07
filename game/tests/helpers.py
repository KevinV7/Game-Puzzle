"""Factories shared by the game tests. Time and randomness are always explicit."""

import random
from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model

from game.models import Game, Level

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds):
    """The instant ``seconds`` after T0 (fractions allowed)."""
    return T0 + timedelta(seconds=seconds)


def seeded_rng(seed=7):
    return random.Random(seed)


def make_user(name="ana"):
    return get_user_model().objects.create_user(name, f"{name}@example.com", "x")


def almost_solved(size):
    """A board one valid move from solved: the gap sits next to the last tile."""
    cells = list(range(1, size * size))
    cells.insert(-1, 0)
    return cells


def winning_tile(size):
    return size * size - 1


def make_game(user, slug="facil", *, board=None, moves_used=0, started_at=T0, **fields):
    """Insert a Game with an explicit board, move count and start time.

    The deadline defaults to ``started_at`` plus the level's time limit.
    """
    level = Level.objects.get(slug=slug)
    board = list(almost_solved(level.size) if board is None else board)
    values = {
        "status": Game.ACTIVE,
        "initial_board": board,
        "board": board,
        "moves_used": moves_used,
        "started_at": started_at,
        "deadline": started_at + timedelta(seconds=level.time_limit_seconds),
    }
    values.update(fields)
    return Game.objects.create(user=user, level=level, **values)


def make_won(user, slug="facil", *, started_at=T0, seconds=60, moves_used=30):
    level = Level.objects.get(slug=slug)
    return make_game(
        user,
        slug,
        started_at=started_at,
        moves_used=moves_used,
        status=Game.WON,
        ended_at=started_at + timedelta(seconds=seconds),
        seconds_remaining=level.time_limit_seconds - seconds,
        moves_remaining=level.move_limit - moves_used,
    )


def make_lost(user, slug="facil", reason=Game.TIMEOUT, *, started_at=T0, seconds=60):
    return make_game(
        user,
        slug,
        started_at=started_at,
        status=Game.LOST,
        loss_reason=reason,
        ended_at=started_at + timedelta(seconds=seconds),
    )


def make_user_who_won(slugs, name="ana"):
    """A user with one won game per slug and no active game."""
    user = make_user(name)
    for slug in slugs:
        make_won(user, slug)
    return user


def make_completed_user(name="ana"):
    return make_user_who_won(["facil", "normal", "dificil"], name)
