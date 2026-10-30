"""Read-only queries and display helpers for the scoring pages.

``scoring`` builds on ``game`` (the score rule lives in ``game.score``); ``game``
never imports this module.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, F, Max, Q, Sum

from game.models import Game, Level
from game.score import POINTS

_MICROSECOND = timedelta(microseconds=1)


def win_percent(won, played):
    """Won over played as a whole percent, halves rounded up. None when nothing was played."""
    if not played:
        return None
    return (200 * won + played) // (2 * played)


def average_seconds(total, played):
    """Mean of ``total`` (a timedelta) over ``played`` attempts, in whole seconds.

    Works on whole microseconds with integer arithmetic and rounds halves up.
    Python's ``round()`` would round halves to even (100.5 s would give 100).
    """
    if not played:
        return None
    microseconds = total // _MICROSECOND
    return (2 * microseconds + played * 1_000_000) // (2 * played * 1_000_000)


def format_clock(seconds):
    """``mm:ss`` with minutes never wrapped into hours."""
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


@dataclass(frozen=True)
class LevelStats:
    """One row of the profile table. Display formatting is left to the view."""

    name: str
    slug: str
    played: int
    won: int
    lost: int
    win_percent: int | None
    best: int | None
    average_seconds: int | None


@dataclass(frozen=True)
class PlayerStats:
    levels: list  # one LevelStats per level, in Level.order
    totals: LevelStats


def _stats_row(name, slug, played, won, total, best):
    return LevelStats(
        name=name,
        slug=slug,
        played=played,
        won=won,
        lost=played - won,
        win_percent=win_percent(won, played),
        best=best,
        average_seconds=average_seconds(total, played),
    )


def player_stats(user):
    """The user's own statistics: one row per level plus a totals row.

    Two queries, however many attempts there are: the levels and one grouped
    aggregate over the finished attempts. Active attempts are not counted and
    nothing is finalized here (the view does that before calling this).
    The totals ``best`` is the SUM of the per-level bests, a display-only figure
    that never feeds a leaderboard query; it is None when no level has a win.
    """
    levels = list(Level.objects.all())
    grouped = {
        row["level_id"]: row
        for row in Game.objects.filter(user=user)
        .exclude(status=Game.ACTIVE)
        .order_by()
        .values("level_id")
        .annotate(
            played=Count("pk"),
            won=Count("pk", filter=Q(status=Game.WON)),
            total=Sum(F("ended_at") - F("started_at")),
            best=Max(POINTS, filter=Q(status=Game.WON)),
        )
    }

    rows = []
    for level in levels:
        row = grouped.get(level.pk)
        if row is None:
            rows.append(_stats_row(level.name, level.slug, 0, 0, timedelta(0), None))
        else:
            rows.append(_stats_row(level.name, level.slug, row["played"], row["won"], row["total"], row["best"]))

    bests = [row.best for row in rows if row.best is not None]
    totals = _stats_row(
        "Totales",
        "",
        sum(row.played for row in rows),
        sum(row.won for row in rows),
        sum((row["total"] for row in grouped.values()), timedelta(0)),
        sum(bests) if bests else None,
    )
    return PlayerStats(levels=rows, totals=totals)
