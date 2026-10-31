"""Leaderboard queries: best attempt per user and level, ranking and viewer position."""

import random

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from game.models import Game, Level
from scoring.queries import leaderboard, position_of, top_ten, viewer_bests

from .helpers import T0, make_game, make_lost, make_user, make_win


class DistinctOnWindowSpikeTests(TestCase):
    """The ORM combination the design flags as unproven.

    ``DISTINCT ON`` picks one attempt per (level, user) inside a ``pk__in``
    subquery, and a ``RowNumber`` window ranks only those attempts, filtered
    outside it. If these fail, ``best_attempts`` falls back to ``ROW_NUMBER``.
    """

    def setUp(self):
        self.ana = make_user("ana")
        self.beto = make_user("beto")
        self.carla = make_user("carla")
        # ana: two Easy wins, the 340 one must be her row. Easy points = s + m.
        make_win(self.ana, "facil", seconds_remaining=100, moves_remaining=100, seconds=10)
        make_win(self.ana, "facil", seconds_remaining=170, moves_remaining=170, seconds=20)
        make_win(self.beto, "facil", seconds_remaining=150, moves_remaining=150, seconds=30)
        make_win(self.carla, "facil", seconds_remaining=100, moves_remaining=50, seconds=40)
        # Normal is x2: (100 + 50) * 2 = 300. Its position must restart at 1.
        make_win(self.carla, "normal", seconds_remaining=100, moves_remaining=50, seconds=50)

    def rows(self):
        return list(top_ten())

    def test_one_row_per_level_and_user_with_the_higher_scoring_attempt(self):
        rows = {(r["level_id"], r["user__username"]): r["points"] for r in self.rows()}
        easy = {name: points for (level, name), points in rows.items() if level == self.level("facil")}
        self.assertEqual(easy, {"ana": 340, "beto": 300, "carla": 150})
        self.assertEqual(len(rows), 4)

    def test_positions_run_from_1_and_restart_on_each_level(self):
        by_level = {}
        for row in self.rows():
            by_level.setdefault(row["level_id"], []).append(row["position"])
        self.assertEqual(sorted(sorted(p) for p in by_level.values()), [[1], [1, 2, 3]])

    def test_rows_are_limited_to_position_10(self):
        for i in range(12):
            make_win(make_user(f"u{i:02d}"), "dificil", seconds_remaining=10 + i, moves_remaining=0)
        hard = [r for r in self.rows() if r["level_id"] == self.level("dificil")]
        self.assertEqual(len(hard), 10)
        self.assertEqual(max(r["position"] for r in hard), 10)

    def test_the_query_ranks_only_best_attempts(self):
        sql = str(top_ten().query)
        subquery_at = sql.index("IN (SELECT DISTINCT ON")
        outer_filter_at = sql.index('"qualify" WHERE "position" <= 10')
        # DISTINCT ON stays inside the pk subquery and keeps its ORDER BY...
        self.assertLess(sql.index("ORDER BY", subquery_at), outer_filter_at)
        # ...the window ranks what that subquery lets through, and the position
        # filter wraps the whole query: it never reaches the subquery.
        self.assertLess(sql.index("ROW_NUMBER"), subquery_at)
        self.assertEqual(sql.index('"position"', subquery_at), outer_filter_at + len('"qualify" WHERE '))
        self.assertTrue(sql.startswith("SELECT * FROM ("))

    def level(self, slug):
        return Level.objects.get(slug=slug).pk


def hour(h):
    """The instant at ``h``:00 on the day of T0."""
    return T0.replace(hour=h)


class OrderingAndVisibilityTests(TestCase):
    """Score order, tie-breaks, one row per user and what never appears."""

    def easy(self):
        return [r for r in top_ten() if r["level_id"] == Level.objects.get(slug="facil").pk]

    def names(self, slug="facil"):
        level_id = Level.objects.get(slug=slug).pk
        return [r["user__username"] for r in top_ten() if r["level_id"] == level_id]

    def win(self, name, points, ended_at, slug="facil"):
        """Win scoring ``points`` on Easy (x1): seconds plus moves."""
        user = self.users.get(name) or self.users.setdefault(name, make_user(name))
        return make_win(user, slug, seconds_remaining=points, moves_remaining=0, ended_at=ended_at)

    def setUp(self):
        self.users = {}

    def test_rows_follow_the_score_descending(self):
        self.win("ana", 300, hour(9))
        self.win("beto", 340, hour(9))
        self.win("carla", 250, hour(9))
        self.assertEqual(self.names(), ["beto", "ana", "carla"])

    def test_a_tie_on_points_goes_to_the_earlier_end_time(self):
        # The later finisher is created first, so insertion order cannot explain a pass.
        self.win("late", 300, hour(11))
        self.win("early", 300, hour(10))
        self.assertEqual(self.names(), ["early", "late"])

    def test_the_tie_break_uses_the_end_time_of_the_attempt_holding_the_best_score(self):
        self.win("beto", 340, hour(10))
        self.win("ana", 340, hour(12))
        self.win("ana", 340, hour(9))
        self.assertEqual(self.names(), ["ana", "beto"])

    def test_equal_points_and_end_times_fall_back_to_the_username(self):
        self.win("zoe", 300, hour(10))
        self.win("ana", 300, hour(10))
        self.win("mia", 300, hour(10))
        self.assertEqual(self.names(), ["ana", "mia", "zoe"])

    def test_positions_are_strict_and_never_shared(self):
        for name in ["d", "c", "b", "a"]:
            self.win(name, 300, hour(10))
        self.assertEqual([r["position"] for r in self.easy()], [1, 2, 3, 4])

    def test_twelve_users_give_exactly_ten_rows_and_the_best_ten(self):
        for i in range(12):
            self.win(f"u{i:02d}", 100 + i, hour(10))
        rows = self.easy()
        self.assertEqual(len(rows), 10)
        self.assertEqual([r["points"] for r in rows], list(range(111, 101, -1)))

    def test_each_user_appears_once_with_their_best_attempt(self):
        for points in (200, 340, 310):
            self.win("ana", points, hour(10))
        rows = self.easy()
        self.assertEqual([(r["user__username"], r["points"]) for r in rows], [("ana", 340)])

    def test_a_level_with_no_wins_has_no_rows(self):
        self.win("ana", 300, hour(10), slug="facil")
        self.assertEqual(self.names("normal"), [])
        self.assertEqual(self.names("dificil"), [])

    def test_a_win_on_one_level_does_not_list_the_user_on_another(self):
        self.win("ana", 300, hour(10), slug="normal")
        self.assertEqual(self.names("facil"), [])
        self.assertEqual(self.names("normal"), ["ana"])

    def test_lost_abandoned_and_active_attempts_never_appear(self):
        ana = make_user("ana")
        for reason in (Game.TIMEOUT, Game.MOVE_LIMIT, Game.ABANDONED):
            make_lost(ana, "facil", reason)
        make_game(ana, "facil")  # active
        beto = make_user("beto")
        make_win(beto, "facil", seconds_remaining=50, moves_remaining=0, ended_at=hour(10))
        make_lost(beto, "facil", Game.MOVE_LIMIT)
        self.assertEqual([(r["user__username"], r["points"]) for r in self.easy()], [("beto", 50)])

    def test_an_expired_unfinalized_attempt_is_not_listed_and_stays_unchanged(self):
        ana = make_user("ana")
        stale = make_game(ana, "facil", started_at=T0)  # deadline long past, never finalized
        self.win("beto", 100, hour(10))
        self.assertEqual(self.names(), ["beto"])
        stale.refresh_from_db()
        self.assertEqual(stale.status, Game.ACTIVE)
        self.assertIsNone(stale.ended_at)

    def test_a_row_carries_exactly_the_public_keys(self):
        self.win("ana", 300, hour(10))
        (row,) = self.easy()
        self.assertEqual(set(row), {"level_id", "user_id", "user__username", "points", "position"})


class ViewerPositionTests(TestCase):
    """The viewer's own best, rows flagged for them and their position outside the top 10."""

    def win(self, user, points, ended_at, slug="facil"):
        """Win on ``slug`` scoring ``points * multiplier`` (seconds plus moves, times the multiplier)."""
        return make_win(user, slug, seconds_remaining=points, moves_remaining=0, ended_at=ended_at)

    def crowd(self, count, points, ended_at, slug="facil", prefix="u"):
        return [self.win(make_user(f"{prefix}{i:02d}"), points, ended_at, slug) for i in range(count)]

    def board(self, user, slug="facil"):
        return next(entry for entry in leaderboard(user) if entry.level.slug == slug)

    def test_viewer_bests_holds_only_the_viewers_best_attempt_per_level(self):
        ana, beto = make_user("ana"), make_user("beto")
        for points in (200, 340, 310):
            self.win(ana, points, hour(10))
        self.win(ana, 100, hour(10), "normal")
        self.win(beto, 999, hour(10))
        bests = {g.level.slug: g.points for g in viewer_bests(ana).select_related("level")}
        self.assertEqual(bests, {"facil": 340, "normal": 200})

    def test_leaderboard_has_one_entry_per_level_in_progression_order_even_when_empty(self):
        entries = leaderboard(make_user("ana"))
        self.assertEqual([e.level.slug for e in entries], ["facil", "normal", "dificil"])
        self.assertTrue(all(e.rows == [] and e.own is None for e in entries))

    def test_rows_are_flagged_only_on_the_viewers_own_row(self):
        ana = make_user("ana")
        self.win(ana, 300, hour(10))
        self.crowd(3, 200, hour(10))
        flags = [(r["user__username"], r["is_viewer"]) for r in self.board(ana).rows]
        self.assertEqual(flags, [("ana", True), ("u00", False), ("u01", False), ("u02", False)])

    def test_the_viewer_at_position_10_is_in_the_rows_and_has_no_own(self):
        viewer = make_user("viki")
        self.crowd(9, 500, hour(10))
        self.win(viewer, 400, hour(10))
        board = self.board(viewer)
        self.assertEqual(len(board.rows), 10)
        self.assertEqual(board.rows[-1]["user__username"], "viki")
        self.assertIsNone(board.own)

    def test_the_viewer_at_position_11_gets_own_with_their_points(self):
        viewer = make_user("viki")
        self.crowd(10, 500, hour(10))
        self.win(viewer, 400, hour(10))
        board = self.board(viewer)
        self.assertEqual(len(board.rows), 10)
        self.assertFalse(any(r["is_viewer"] for r in board.rows))
        self.assertEqual((board.own.position, board.own.points), (11, 400))

    def test_the_viewer_at_position_14_with_a_tie_matches_a_hand_count(self):
        viewer = make_user("viki")
        self.crowd(11, 500, hour(10))  # 11 users ahead on points
        self.win(make_user("aaron"), 300, hour(9))  # earlier end: ahead
        self.win(make_user("bruno"), 300, hour(10))  # same end, smaller username: ahead
        self.win(make_user("zed"), 300, hour(10))  # same end, larger username: behind
        self.win(make_user("yan"), 300, hour(11))  # later end: behind
        self.win(viewer, 300, hour(10))
        self.assertEqual(self.board(viewer).own.position, 14)

    def test_ten_users_at_400_and_the_viewer_at_400_reached_later_gives_11(self):
        viewer = make_user("aaa")  # the smallest username, so only the end time can place them
        self.crowd(10, 400, hour(10))
        self.win(viewer, 400, hour(11))
        self.assertEqual(self.board(viewer).own.position, 11)

    def test_the_username_decides_when_points_and_end_times_are_equal(self):
        late_name, early_name = make_user("zed"), make_user("aaa")
        self.crowd(10, 400, hour(10), prefix="m")
        self.win(late_name, 400, hour(10))
        self.win(early_name, 400, hour(10))
        self.assertEqual(self.board(late_name).own.position, 12)
        self.assertIsNone(self.board(early_name).own)
        self.assertEqual(self.board(early_name).rows[0]["user__username"], "aaa")

    def test_a_viewer_without_a_win_on_the_level_has_no_own(self):
        viewer = make_user("viki")
        self.crowd(11, 500, hour(10))
        make_lost(viewer, "facil", Game.TIMEOUT)
        self.win(viewer, 100, hour(10), "normal")  # a win elsewhere does not count here
        self.assertIsNone(self.board(viewer).own)
        self.assertIsNone(self.board(viewer, "dificil").own)

    def test_position_of_counts_users_not_attempts(self):
        viewer, rival = make_user("viki"), make_user("rival")
        for points in (400, 410, 420):
            self.win(rival, points, hour(10))
        self.win(viewer, 300, hour(10))
        best = viewer_bests(viewer).get()
        self.assertEqual(position_of(viewer, best.level, best), 2)

    def test_position_of_matches_a_plain_python_ranking_for_every_user(self):
        rng = random.Random(2026)
        for level in Level.objects.all():
            attempts = {}
            for i in range(15):
                user = make_user(f"{level.slug[:1]}{rng.randrange(100):02d}x{i}")
                for _ in range(rng.randint(1, 3)):
                    points = rng.choice([100, 150, 200])
                    ended_at = hour(rng.choice([8, 9, 10]))
                    make_win(user, level.slug, seconds_remaining=points, moves_remaining=0, ended_at=ended_at)
                    attempts.setdefault(user, []).append((-points * level.multiplier, ended_at))
            order = sorted(attempts, key=lambda u: (min(attempts[u]), u.username))
            for rank, user in enumerate(order, start=1):
                best = viewer_bests(user).get(level=level)
                self.assertEqual(position_of(user, level, best), rank, user.username)
            expected = [(u.username, -min(attempts[u])[0], r) for r, u in enumerate(order[:10], start=1)]
            rows = [r for r in top_ten() if r["level_id"] == level.pk]
            self.assertEqual([(r["user__username"], r["points"], r["position"]) for r in rows], expected)


class LeaderboardQueryCountTests(TestCase):
    """Query counts depend on the viewer's levels, never on the number of players."""

    def count(self, user):
        with CaptureQueriesContext(connection) as queries:
            leaderboard(user)
        return len(queries)

    def players(self, count, first=0):
        """``count`` players, each with a win on every level, numbered from ``first``."""
        for i in range(first, first + count):
            user = make_user(f"p{i:02d}")
            for slug in ("facil", "normal", "dificil"):
                make_win(user, slug, seconds_remaining=500 - i, moves_remaining=0, ended_at=hour(10))

    def test_a_viewer_with_no_win_runs_the_three_base_queries(self):
        viewer = make_user("viki")
        self.players(3)
        with self.assertNumQueries(3):
            leaderboard(viewer)

    def test_the_count_is_the_same_for_3_players_as_for_12(self):
        viewer = make_user("viki")
        self.players(3)
        few = self.count(viewer)
        self.players(9, first=3)
        self.assertEqual(few, self.count(viewer))

    def test_a_viewer_inside_the_top_10_adds_no_position_query(self):
        viewer = make_user("viki")
        self.players(3)
        for slug in ("facil", "normal", "dificil"):
            make_win(viewer, slug, seconds_remaining=900, moves_remaining=0, ended_at=hour(10))
        with self.assertNumQueries(3):
            leaderboard(viewer)

    def test_each_level_where_the_viewer_is_outside_the_top_10_adds_exactly_one_query(self):
        viewer = make_user("viki")
        self.players(12)
        for expected, slug in enumerate(("facil", "normal", "dificil"), start=4):
            make_win(viewer, slug, seconds_remaining=1, moves_remaining=0, ended_at=hour(10))
            with self.assertNumQueries(expected):
                leaderboard(viewer)
