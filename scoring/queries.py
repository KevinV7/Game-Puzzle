"""Read-only queries and display helpers for the scoring pages.

``scoring`` builds on ``game`` (the score rule lives in ``game.score``); ``game``
never imports this module.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Count, F, Max, Q, Sum, Window
from django.db.models.functions import RowNumber

from game.models import Game, Level
from game.score import POINTS, won_with_points

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


def best_attempts():
    """One won attempt per (level, user): the most points, then the earliest end.

    PostgreSQL ``DISTINCT ON``. The attempt kept is the one the leaderboard
    ranks, so its ``ended_at`` is the tie-break against other users.
    """
    return (
        won_with_points()
        .order_by("level_id", "user_id", "-points", "ended_at", "pk")
        .distinct("level_id", "user_id")
    )


def top_ten():
    """The first ten ranked best attempts of every level, as dicts.

    Only best attempts are ranked (``pk__in`` filters before the window), and
    the position restarts on each level. The order is a strict total one:
    points descending, then the earlier end of the best attempt, then the
    username (unique), so no two rows share a position.
    """
    return (
        Game.objects.filter(pk__in=best_attempts().values("pk"))
        .annotate(
            points=POINTS,
            position=Window(
                RowNumber(),
                partition_by=F("level_id"),
                order_by=[F("points").desc(), F("ended_at").asc(), F("user__username").asc()],
            ),
        )
        .filter(position__lte=10)
        .order_by("level_id", "position")
        .values("level_id", "user_id", "user__username", "points", "position")
    )


def viewer_bests(user):
    """The viewer's own best attempt on each level they have won, with ``points``.

    Filtering by user before ``DISTINCT ON`` is safe here because the user is
    part of the partition.
    """
    return best_attempts().filter(user=user)


def position_of(user, level, best):
    """The viewer's strict position on ``level``: 1 plus the users who beat ``best``.

    ``best`` is the viewer's best attempt on that level (from ``viewer_bests``).
    A user beats it exactly when that user's own best does, because the order
    compares one user's attempts lexicographically, so no best-attempt
    subquery is needed. This is a count on purpose: filtering the ranked
    ``top_ten`` queryset by user would rank the viewer before the other users
    are counted and always return position 1.
    """
    beaten_by = (
        Q(points__gt=best.points)
        | Q(points=best.points, ended_at__lt=best.ended_at)
        | Q(points=best.points, ended_at=best.ended_at, user__username__lt=user.username)
    )
    others = won_with_points().filter(level=level).exclude(user=user).filter(beaten_by)
    return 1 + others.order_by().values("user").distinct().count()


@dataclass(frozen=True)
class OwnPosition:
    """The viewer's place on a level where they are not in the visible rows."""

    position: int
    points: int


@dataclass(frozen=True)
class LevelBoard:
    """One leaderboard table.

    ``rows`` are the ``top_ten`` dicts for the level plus ``is_viewer``;
    ``own`` is None when the viewer has no win on the level or is already in ``rows``.
    """

    level: Level
    rows: list
    own: OwnPosition | None


def leaderboard(user):
    """One ``LevelBoard`` per level, in ``Level.order``.

    Three queries (levels, top ten, viewer bests) however many players there
    are, plus one count for each level where the viewer has a win but is
    outside the top ten.
    """
    levels = list(Level.objects.all())
    rows_by_level = {level.pk: [] for level in levels}
    for row in top_ten():
        rows_by_level[row["level_id"]].append({**row, "is_viewer": row["user_id"] == user.pk})
    bests = {game.level_id: game for game in viewer_bests(user)}

    boards = []
    for level in levels:
        rows = rows_by_level[level.pk]
        best = bests.get(level.pk)
        own = None
        if best is not None and not any(row["is_viewer"] for row in rows):
            own = OwnPosition(position_of(user, level, best), best.points)
        boards.append(LevelBoard(level=level, rows=rows, own=own))
    return boards
