from django.shortcuts import render
from django.views.decorators.http import require_GET

from game import services

from . import queries
from .queries import format_clock, player_stats

DASH = "—"


def _display_row(stats, *, is_total=False):
    """One table row as strings. Counts show 0 and the rest show a dash when empty."""
    return {
        "name": stats.name,
        "is_total": is_total,
        "played": stats.played,
        "won": stats.won,
        "lost": stats.lost,
        "win_percent": DASH if stats.win_percent is None else f"{stats.win_percent} %",
        "best": DASH if stats.best is None else stats.best,
        "has_best": stats.best is not None,
        "average": DASH if stats.average_seconds is None else format_clock(stats.average_seconds),
    }


# No login decorator: LoginRequiredMiddleware already denies anonymous access.
@require_GET
def profile(request):
    # An expired attempt counts as a timeout loss even if nothing finalized it yet.
    # It only touches the viewer's own attempt and never starts a new one.
    services.finalize_expired(request.user)
    stats = player_stats(request.user)
    rows = [_display_row(level) for level in stats.levels]
    rows.append(_display_row(stats.totals, is_total=True))
    return render(request, "scoring/profile.html", {"rows": rows, "has_finished": stats.totals.played > 0})


# No login decorator: LoginRequiredMiddleware already denies anonymous access.
@require_GET
def leaderboard(request):
    # Read-only: an expired attempt of any user never has a score, so nothing is finalized.
    return render(request, "scoring/leaderboard.html", {"boards": queries.leaderboard(request.user)})
