"""Thin HTTP layer over ``game.services``.

Views translate requests into service calls and results into responses. Every rule
(availability, timing, move limits, progression) lives in the service layer. Each
view reads the clock once and passes it down, so the numbers a response reports
are consistent with the attempt the service just created or changed.
"""

import json

from django.http import HttpResponseRedirect, JsonResponse
from django.http.response import HttpResponseRedirectBase
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from game import services
from game.models import Game, Level

LEVEL_NOT_FOUND = "Nivel no encontrado"


class HttpResponseSeeOther(HttpResponseRedirectBase):
    """303: the browser follows a POST with a GET of the new location."""

    status_code = 303


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


# HTML pages


def _error_page(request, status, title, message):
    return render(request, "game/error.html", {"title": title, "message": message}, status=status)


def _time_label(seconds):
    return f"{seconds // 60} min" if seconds % 60 == 0 else f"{seconds} s"


def _clock(seconds):
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


@require_GET
def play(request):
    catalog = services.levels_for(request.user)
    rows, previous = [], None
    for entry in catalog.levels:
        locked = not entry.playable and not entry.passed
        rows.append(
            {
                "entry": entry,
                "time": _time_label(entry.level.time_limit_seconds),
                "unlock_hint": f"Gana {previous.name} para desbloquear" if locked and previous else "",
            }
        )
        previous = entry.level
    return render(request, "game/levels.html", {"catalog": catalog, "rows": rows})


@require_POST
def game_start(request, slug):
    level = Level.objects.filter(slug=slug).first()
    if level is None:
        return _error_page(request, 404, LEVEL_NOT_FOUND, "Ese nivel no existe.")
    try:
        game = services.start(request.user, level, confirm=request.POST.get("confirmar") == "1")
    except services.LevelLocked:
        return _error_page(request, 403, "Nivel bloqueado", "Ese nivel no está disponible para ti todavía.")
    except services.ConfirmRequired:
        active = services.levels_for(request.user).active_game
        cancel_url = reverse("game-board", args=[active.level.slug]) if active else reverse("play")
        return render(request, "game/confirm_switch.html", {"level": level, "cancel_url": cancel_url}, status=409)
    return HttpResponseSeeOther(reverse("game-board", args=[game.level.slug]))


@require_GET
def game_board(request, slug):
    level = Level.objects.filter(slug=slug).first()
    if level is None:
        return _error_page(request, 404, LEVEL_NOT_FOUND, "Ese nivel no existe.")
    game_id = (
        Game.objects.filter(user=request.user, level=level, status=Game.ACTIVE).values_list("pk", flat=True).first()
    )
    if game_id is None:
        return HttpResponseRedirect(reverse("play"))
    now = timezone.now()
    try:
        outcome = services.state(request.user, game_id, now=now)
    except Game.DoesNotExist:
        return HttpResponseRedirect(reverse("play"))
    data = _payload(outcome, now)
    game = outcome.game
    context = {
        "game": game,
        "level": game.level,
        "state": data,
        "cells": game.board,
        "clock": _clock(data["game"]["seconds_remaining"]),
        "can_switch": outcome.can_switch,
    }
    return render(request, "game/board.html", context)
