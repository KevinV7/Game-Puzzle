"""Score rule and player statistics queries."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from game.models import Game, Level
from game.score import won_with_points

from .helpers import make_game, make_lost, make_user, make_win


def points_of(game):
    return won_with_points().get(pk=game.pk).points


class PointsTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")

    def test_easy_win_scores_remaining_seconds_plus_moves(self):
        game = make_win(self.ana, "facil", seconds_remaining=80, moves_remaining=260)
        self.assertEqual(points_of(game), 340)

    def test_hard_win_applies_the_level_multiplier(self):
        game = make_win(self.ana, "dificil", seconds_remaining=100, moves_remaining=400)
        self.assertEqual(points_of(game), 1500)

    def test_normal_win_is_doubled(self):
        game = make_win(self.ana, "normal", seconds_remaining=100, moves_remaining=50)
        self.assertEqual(points_of(game), 300)

    def test_a_win_with_nothing_left_scores_zero_not_null_and_not_negative(self):
        game = make_win(self.ana, "facil", seconds_remaining=0, moves_remaining=0)
        points = points_of(game)
        self.assertIsNotNone(points)
        self.assertEqual(points, 0)

    def test_lost_attempts_of_every_reason_have_no_score_row(self):
        losses = [make_lost(self.ana, "facil", reason) for reason in (Game.TIMEOUT, Game.MOVE_LIMIT, Game.ABANDONED)]
        for game in losses:
            self.assertFalse(won_with_points().filter(pk=game.pk).exists(), game.loss_reason)
        self.assertEqual(won_with_points().count(), 0)

    def test_an_active_attempt_has_no_score_row(self):
        game = make_game(self.ana, "facil")
        self.assertFalse(won_with_points().filter(pk=game.pk).exists())

    def test_changing_the_level_multiplier_changes_the_points_without_rewriting_rows(self):
        game = make_win(self.ana, "facil", seconds_remaining=80, moves_remaining=260)
        stored_before = Game.objects.filter(pk=game.pk).values().get()
        self.assertEqual(points_of(game), 340)

        Level.objects.filter(slug="facil").update(multiplier=5)

        self.assertEqual(points_of(game), 1700)
        self.assertEqual(Game.objects.filter(pk=game.pk).values().get(), stored_before)

    def test_no_score_is_stored_on_the_attempt(self):
        names = {field.name for field in Game._meta.get_fields()}
        self.assertFalse({"score", "points"} & names)

    def test_the_migration_set_is_complete(self):
        # makemigrations --check exits non-zero (SystemExit) when a model change lacks a migration.
        call_command("makemigrations", check=True, dry_run=True, stdout=StringIO())
