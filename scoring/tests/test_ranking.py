"""Leaderboard queries: best attempt per user and level, ranking and viewer position."""

from django.test import TestCase

from game.models import Level
from scoring.queries import top_ten

from .helpers import make_user, make_win


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
