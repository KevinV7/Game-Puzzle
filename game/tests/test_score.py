"""score_breakdown: the one-attempt view of the score rule used by the win panel."""

from django.test import TestCase

from game.models import Game
from game.score import score_breakdown
from game.tests.helpers import at, make_game, make_lost, make_user, make_won

BREAKDOWN_KEYS = {"seconds_remaining", "moves_remaining", "multiplier", "points"}


class ScoreBreakdownTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")

    def won(self, slug="facil", *, seconds_remaining, moves_remaining):
        return make_game(
            self.ana,
            slug,
            status=Game.WON,
            ended_at=at(60),
            seconds_remaining=seconds_remaining,
            moves_remaining=moves_remaining,
        )

    def test_an_active_game_has_no_score_and_costs_no_query(self):
        game = make_game(self.ana)
        with self.assertNumQueries(0):
            self.assertIsNone(score_breakdown(game))

    def test_a_lost_game_has_no_score_and_costs_no_query(self):
        for reason in [Game.TIMEOUT, Game.MOVE_LIMIT, Game.ABANDONED]:
            with self.subTest(reason=reason):
                game = make_lost(make_user(f"u_{reason}"), reason=reason)
                with self.assertNumQueries(0):
                    self.assertIsNone(score_breakdown(game))

    def test_a_won_game_returns_exactly_the_four_integers_in_one_query(self):
        game = self.won(seconds_remaining=120, moves_remaining=85)
        with self.assertNumQueries(1):
            breakdown = score_breakdown(game)
        self.assertEqual(set(breakdown), BREAKDOWN_KEYS)
        self.assertEqual(
            breakdown, {"seconds_remaining": 120, "moves_remaining": 85, "multiplier": 1, "points": 205}
        )
        for value in breakdown.values():
            self.assertIs(type(value), int)

    def test_the_multiplier_comes_from_the_level(self):
        normal = score_breakdown(self.won("normal", seconds_remaining=120, moves_remaining=85))
        self.assertEqual((normal["multiplier"], normal["points"]), (2, 410))

    def test_a_win_with_nothing_left_scores_zero_not_none(self):
        breakdown = score_breakdown(self.won(seconds_remaining=0, moves_remaining=0))
        self.assertEqual(breakdown["points"], 0)

    def test_it_reads_the_stored_attempt_not_the_instance_in_memory(self):
        game = make_won(self.ana)
        Game.objects.filter(pk=game.pk).update(seconds_remaining=10, moves_remaining=5)
        self.assertEqual(score_breakdown(game)["points"], 15)
