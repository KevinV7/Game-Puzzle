"""Thin HTTP layer over ``game.services``.

Views translate requests into service calls and results into responses. Every rule
(availability, timing, move limits, progression) lives in the service layer. Each
view reads the clock once and passes it down, so the numbers a response reports
are consistent with the attempt the service just created or changed.
"""

import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from game import services
from game.models import Game

# Serialization


def _game_data(game, now):
    if game.status == Game.ACTIVE:
        seconds = services.remaining_seconds(game, now)
    else:
        seconds = game.seconds_remaining or 0
    return {
        "id": game.id,
        "level": game.level.slug,
        "size": game.level.size,
        "board": game.board,
        "moves_used": game.moves_used,
        "move_limit": game.level.move_limit,
        "seconds_remaining": seconds,
        "status": game.status,
        "loss_reason": game.loss_reason or None,
    }


def _ended_data(game):
    return {"id": game.id, "level": game.level.slug, "status": game.status, "loss_reason": game.loss_reason or None}


def _payload(outcome, now):
    """The one response shape of the state and move endpoints."""
    move = None
    if outcome.move is not None:
        move = {"tile": outcome.move.tile, "valid": outcome.move.valid, "tile_in_place": outcome.tile_in_place}
    return {
        "game": _game_data(outcome.game, now),
        "move": move,
        "ended": None if outcome.ended is None else _ended_data(outcome.ended),
        "next_level": None if outcome.next_level is None else outcome.next_level.slug,
        "completed_all": outcome.completed_all,
        "can_switch": outcome.can_switch,
    }


def _json_error(code, status, **extra):
    return JsonResponse({"error": code, **extra}, status=status)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


# JSON API


@require_GET
def game_state(request, game_id):
    now = timezone.now()
    try:
        outcome = services.state(request.user, game_id, now=now)
    except Game.DoesNotExist:
        return _json_error("not_found", 404)
    return JsonResponse(_payload(outcome, now))


@require_POST
def game_move(request, game_id):
    now = timezone.now()
    # The game is read first so that a foreign or unknown game is a 404 whatever the
    # body says, and so that the tile can be checked against the board size.
    game = Game.objects.select_related("level").filter(user=request.user, pk=game_id).first()
    if game is None:
        return _json_error("not_found", 404)
    try:
        body = json.loads(request.body)
    except ValueError:
        return _json_error("bad_request", 400)
    if not isinstance(body, dict):
        return _json_error("bad_request", 400)
    tile, seq = body.get("tile"), body.get("seq")
    if not (_is_int(tile) and _is_int(seq)) or not 1 <= tile < game.level.size**2:
        return _json_error("bad_request", 400)
    try:
        outcome = services.move(request.user, game_id, tile, seq, now=now)
    except Game.DoesNotExist:
        return _json_error("not_found", 404)
    except services.Stale:
        current = services.state(request.user, game_id, now=now)
        return _json_error("stale", 409, **_payload(current, now))
    return JsonResponse(_payload(outcome, now))
