"""Leaderboard queries: best attempt per user and level, ranking and viewer position."""

from django.test import TestCase

from game.models import Game, Level
from scoring.queries import top_ten

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
