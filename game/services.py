"""Attempt lifecycle and level progression.

Every public function runs in one transaction, locks the user's row first (a per-user
mutex, so start, move and expiry never interleave for one user) and then the game.
Time and randomness are injectable: ``now`` is a timezone-aware datetime and ``rng`` a
``random.Random``. The caller never decides a rule; it only passes the request.
"""

import random
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from game import engine
from game.models import Game, Level

# Order of the last level. A user who won it has completed the game, for good.
FINAL_ORDER = 3


class LevelLocked(Exception):
    """The level cannot be started by this user now."""


class ConfirmRequired(Exception):
    """A completed user switches level without confirming the abandonment."""


class Stale(Exception):
    """The game is finished or the client's move number is out of date."""


@dataclass(frozen=True)
class LevelEntry:
    level: Level
    playable: bool
    passed: bool
    current: bool
    active_game: Game | None


@dataclass(frozen=True)
class Catalog:
    levels: list[LevelEntry]
    completed: bool
    current_level: Level | None
    active_game: Game | None


# Progression is derived from won games, so it can never drift from them.


def won_orders(user):
    """The distinct ``order`` values of the levels the user has won."""
    return set(Game.objects.filter(user=user, status=Game.WON).values_list("level__order", flat=True))


def completed(user):
    return _is_completed(won_orders(user))


def current_order(user):
    """The one level a not-completed user may play, or None once completed."""
    return _current_order(won_orders(user))


def playable(user, level):
    return _is_playable(level.order, won_orders(user))


def _is_completed(won):
    return FINAL_ORDER in won


def _current_order(won):
    return None if _is_completed(won) else max(won, default=0) + 1


def _is_playable(order, won):
    return _is_completed(won) or order == _current_order(won)


@transaction.atomic
def levels_for(user, now=None):
    """The catalog with per-level flags for ``user``.

    An expired active game is finalized as a timeout but never restarted, so viewing
    the level selection never starts a clock.
    """
    now = now or timezone.now()
    _lock_user(user)
    active = _active_game(user)
    if active is not None and _expire(active, now):
        active = None
    won = won_orders(user)
    current = _current_order(won)
    entries = [
        LevelEntry(
            level=level,
            playable=_is_playable(level.order, won),
            passed=level.order in won,
            current=level.order == current,
            active_game=active if active is not None and active.level_id == level.id else None,
        )
        for level in Level.objects.all()
    ]
    return Catalog(
        levels=entries,
        completed=_is_completed(won),
        current_level=next((entry.level for entry in entries if entry.current), None),
        active_game=active,
    )


@transaction.atomic
def finalize_expired(user, now=None):
    """Finalize the user's expired active game as a timeout.

    Returns the finalized game, or None if there was nothing to finalize. Idempotent:
    a second call finds no active game. Other features call this before aggregating.
    """
    now = now or timezone.now()
    _lock_user(user)
    active = _active_game(user)
    if active is not None and _expire(active, now):
        return active
    return None


@transaction.atomic
def start(user, level, confirm=False, now=None, rng=None):
    """Start ``level`` for ``user`` and return the active game.

    Rules, in order: expire a stale game, check availability, return an active game
    on the same level unchanged, otherwise abandon the active game on another level
    (completed users only, with ``confirm``) and create a fresh attempt.
    """
    now = now or timezone.now()
    _lock_user(user)
    active = _active_game(user)
    if active is not None and _expire(active, now):
        active = None
    if not _is_playable(level.order, won_orders(user)):
        raise LevelLocked(level.slug)
    if active is not None:
        if active.level_id == level.id:
            return active
        if not confirm:
            raise ConfirmRequired(level.slug)
        _finish(active, Game.LOST, Game.ABANDONED, now)
    return _create_game(user, level, now, rng)


def _lock_user(user):
    get_user_model().objects.select_for_update().get(pk=user.pk)


def _active_game(user):
    # of=("self",) locks the game row only, not the shared Level rows it joins.
    games = Game.objects.select_for_update(of=("self",)).select_related("level")
    return games.filter(user=user, status=Game.ACTIVE).first()


def _expire(game, now):
    """Finalize ``game`` as a timeout if its deadline has passed; True if it did.

    The end time is the deadline, not the moment the server noticed.
    """
    if game.status != Game.ACTIVE or now < game.deadline:
        return False
    _finish(game, Game.LOST, Game.TIMEOUT, game.deadline)
    return True


def _finish(game, status, loss_reason, ended_at):
    game.status = status
    game.loss_reason = loss_reason
    game.ended_at = ended_at
    game.save(update_fields=["status", "loss_reason", "ended_at"])


def _create_game(user, level, now, rng):
    board = list(engine.shuffle(level.size, rng or random.Random()))
    return Game.objects.create(
        user=user,
        level=level,
        initial_board=board,
        board=board,
        started_at=now,
        deadline=now + timedelta(seconds=level.time_limit_seconds),
    )
