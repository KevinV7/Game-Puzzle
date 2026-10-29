"""Attempt lifecycle and level progression.

Every public function runs in one transaction, locks the user's row first (a per-user
mutex, so start, move and expiry never interleave for one user) and then the game.
Time and randomness are injectable: ``now`` is a timezone-aware datetime and ``rng`` a
``random.Random``. The caller never decides a rule; it only passes the request.
"""

import math
import random
from dataclasses import dataclass, replace
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from game import engine
from game.models import Game, Level, Move

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


@dataclass(frozen=True)
class Outcome:
    """What a state or move request tells the client.

    ``game`` is the attempt to render now: the replacement after a loss, the won
    attempt after a win. ``ended`` is the lost attempt a loss just finished, else
    None. ``move`` is None when no move was applied (state reads, or a move that
    arrived after the deadline). ``tile_in_place`` is False for invalid moves.
    """

    game: Game
    move: Move | None = None
    tile_in_place: bool = False
    ended: Game | None = None
    next_level: Level | None = None
    completed_all: bool = False
    can_switch: bool = False


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


@transaction.atomic
def state(user, game_id, now=None, rng=None):
    """Return the state of one of the user's games.

    Raises ``Game.DoesNotExist`` for an unknown or foreign game. An expired game is
    finalized as a timeout and replaced by a fresh attempt of the same level, whose
    clock starts now.
    """
    now = now or timezone.now()
    _lock_user(user)
    game = _get_game(user, game_id)
    if _expire(game, now):
        return _replaced(user, game, now, rng)
    return Outcome(game=game, can_switch=completed(user))


@transaction.atomic
def move(user, game_id, tile, seq, now=None, rng=None):
    """Apply the move of ``tile`` to one of the user's games.

    ``seq`` is the move number the client expects next. Rules, in order: a finished
    game or a wrong ``seq`` raises ``Stale``; a move at or after the deadline is not
    applied and the game times out; otherwise the move is logged (invalid moves too)
    and may win the game or lose it by the move limit. A loss starts a replacement.
    ``tile`` must be a numbered tile of the board (the view answers 400 otherwise).
    """
    now = now or timezone.now()
    _lock_user(user)
    game = _get_game(user, game_id)
    if game.status != Game.ACTIVE:
        raise Stale("the game is finished")
    if _expire(game, now):
        return _replaced(user, game, now, rng)
    if seq != game.moves_used + 1:
        raise Stale("unexpected move number")
    level = game.level
    if not 1 <= tile < level.size * level.size:
        raise ValueError(f"tile {tile!r} is not on the board")

    board = tuple(game.board)
    try:
        new_board = engine.apply_move(board, level.size, tile)
        valid = True
    except engine.InvalidMove:
        new_board, valid = board, False
    game.board = list(new_board)
    game.moves_used += 1
    game.save(update_fields=["board", "moves_used"])
    entry = Move.objects.create(game=game, seq=game.moves_used, tile=tile, valid=valid, created_at=now)
    in_place = valid and engine.tile_in_place(new_board, tile)

    if valid and engine.is_solved(new_board):
        return _won(user, game, entry, in_place, now)
    if game.moves_used >= level.move_limit:
        _finish(game, Game.LOST, Game.MOVE_LIMIT, now)
        return replace(_replaced(user, game, now, rng), move=entry, tile_in_place=in_place)
    return Outcome(game=game, move=entry, tile_in_place=in_place, can_switch=completed(user))


def remaining_seconds(game, now=None):
    """Whole seconds left on the server clock, rounded down and never negative."""
    now = now or timezone.now()
    return max(0, math.floor((game.deadline - now).total_seconds()))


def _won(user, game, entry, in_place, now):
    level = game.level
    was_completed = completed(user)
    game.status = Game.WON
    game.ended_at = now
    game.seconds_remaining = remaining_seconds(game, now)
    game.moves_remaining = level.move_limit - game.moves_used
    game.save(update_fields=["status", "ended_at", "seconds_remaining", "moves_remaining"])
    # Only a user who had not completed the game is sent on to a next level.
    next_level = None
    if not was_completed and level.order < FINAL_ORDER:
        next_level = Level.objects.get(order=level.order + 1)
    return Outcome(
        game=game,
        move=entry,
        tile_in_place=in_place,
        next_level=next_level,
        completed_all=not was_completed and level.order == FINAL_ORDER,
        can_switch=completed(user),
    )


def _replaced(user, finished, now, rng):
    """The outcome of a loss: a fresh attempt of the same level next to the lost one."""
    return Outcome(
        game=_create_game(user, finished.level, now, rng),
        ended=finished,
        can_switch=completed(user),
    )


def _get_game(user, game_id):
    games = Game.objects.select_for_update(of=("self",)).select_related("level")
    return games.get(user=user, pk=game_id)


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
