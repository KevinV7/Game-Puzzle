"""Score rule and player statistics queries."""

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from game.models import Game, Level
from game.score import won_with_points
from scoring.queries import average_seconds, format_clock, player_stats, win_percent

from .helpers import at, make_game, make_lost, make_user, make_win


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


class FormatHelperTests(SimpleTestCase):
    def test_win_percent_rounds_half_up(self):
        cases = [(1, 3, 33), (3, 7, 43), (1, 8, 13), (1, 2, 50), (0, 5, 0), (5, 5, 100)]
        for won, played, expected in cases:
            with self.subTest(won=won, played=played):
                self.assertEqual(win_percent(won, played), expected)

    def test_win_percent_is_an_integer(self):
        self.assertIsInstance(win_percent(1, 8), int)

    def test_win_percent_is_none_when_nothing_was_played(self):
        self.assertIsNone(win_percent(0, 0))

    def test_average_seconds_rounds_to_the_nearest_second_with_halves_up(self):
        cases = [
            (timedelta(seconds=201), 2, 101),  # 100.5 s: round() would give 100
            (timedelta(seconds=5), 2, 3),  # 2.5 s: round() would give 2
            (timedelta(seconds=1), 2, 1),  # 0.5 s: round() would give 0
            (timedelta(seconds=200, microseconds=999_800), 2, 100),  # 100.4999 s
            (timedelta(seconds=100), 1, 100),
            (timedelta(seconds=300), 3, 100),
            (timedelta(seconds=100) + timedelta(seconds=101), 2, 101),
        ]
        for total, played, expected in cases:
            with self.subTest(total=total, played=played):
                self.assertEqual(average_seconds(total, played), expected)

    def test_average_seconds_is_an_integer(self):
        self.assertIsInstance(average_seconds(timedelta(seconds=201), 2), int)

    def test_average_seconds_is_none_when_nothing_was_played(self):
        self.assertIsNone(average_seconds(timedelta(0), 0))
        self.assertIsNone(average_seconds(None, 0))

    def test_format_clock_is_zero_padded_minutes_and_seconds(self):
        cases = [(0, "00:00"), (9, "00:09"), (61, "01:01"), (100, "01:40"), (720, "12:00"), (3600, "60:00")]
        for seconds, expected in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(format_clock(seconds), expected)

    def test_format_clock_never_wraps_minutes_into_hours(self):
        self.assertEqual(format_clock(3 * 3600 + 5), "180:05")


class PlayerStatsCountTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")

    def rows(self, stats):
        return {row.slug: row for row in stats.levels}

    def test_one_row_per_level_in_progression_order_plus_a_totals_row(self):
        stats = player_stats(self.ana)
        self.assertEqual([row.slug for row in stats.levels], ["facil", "normal", "dificil"])
        self.assertEqual([row.name for row in stats.levels], ["Fácil", "Normal", "Difícil"])
        self.assertEqual(stats.totals.name, "Totales")

    def test_hand_computed_easy_row(self):
        make_win(self.ana, "facil", seconds_remaining=80, moves_remaining=260)  # 340
        make_win(self.ana, "facil", seconds_remaining=50, moves_remaining=260)  # 310
        make_lost(self.ana, "facil", Game.TIMEOUT)
        make_lost(self.ana, "facil", Game.ABANDONED)

        rows = self.rows(player_stats(self.ana))

        easy = rows["facil"]
        self.assertEqual((easy.played, easy.won, easy.lost), (4, 2, 2))
        self.assertEqual(easy.win_percent, 50)
        self.assertEqual(easy.best, 340)
        for slug in ("normal", "dificil"):
            row = rows[slug]
            self.assertEqual((row.played, row.won, row.lost), (0, 0, 0), slug)
            self.assertIsNone(row.win_percent, slug)
            self.assertIsNone(row.best, slug)

    def test_an_active_attempt_is_not_counted(self):
        make_lost(self.ana, "facil", Game.TIMEOUT)
        make_win(self.ana, "facil", seconds_remaining=10, moves_remaining=10)
        make_game(self.ana, "facil")

        easy = self.rows(player_stats(self.ana))["facil"]

        self.assertEqual((easy.played, easy.won, easy.lost), (2, 1, 1))

    def test_a_move_limit_loss_counts_as_lost(self):
        make_lost(self.ana, "facil", Game.MOVE_LIMIT)

        easy = self.rows(player_stats(self.ana))["facil"]

        self.assertEqual((easy.played, easy.won, easy.lost), (1, 0, 1))
        self.assertEqual(easy.win_percent, 0)

    def test_an_expired_unfinalized_attempt_is_not_counted_and_not_finalized(self):
        game = make_game(self.ana, "facil", started_at=at(-3600))
        self.assertLess(game.deadline, at(0))

        easy = self.rows(player_stats(self.ana))["facil"]

        self.assertEqual(easy.played, 0)
        game.refresh_from_db()
        self.assertEqual(game.status, Game.ACTIVE)
        self.assertIsNone(game.ended_at)

    def test_won_plus_lost_equals_played_in_every_row_including_totals(self):
        make_win(self.ana, "facil", seconds_remaining=1, moves_remaining=1)
        make_lost(self.ana, "facil", Game.TIMEOUT)
        make_lost(self.ana, "normal", Game.MOVE_LIMIT)
        make_lost(self.ana, "normal", Game.ABANDONED)
        make_win(self.ana, "dificil", seconds_remaining=1, moves_remaining=1)
        make_game(self.ana, "dificil")

        stats = player_stats(self.ana)

        for row in [*stats.levels, stats.totals]:
            self.assertEqual(row.won + row.lost, row.played, row.name)

    def test_other_users_attempts_are_never_counted(self):
        beto = make_user("beto")
        make_win(beto, "facil", seconds_remaining=500, moves_remaining=500)
        make_lost(beto, "facil", Game.TIMEOUT)
        make_lost(beto, "normal", Game.ABANDONED)
        make_win(self.ana, "facil", seconds_remaining=10, moves_remaining=10)

        stats = player_stats(self.ana)

        self.assertEqual((stats.totals.played, stats.totals.won, stats.totals.lost), (1, 1, 0))
        self.assertEqual(self.rows(stats)["facil"].best, 20)
        self.assertEqual(self.rows(stats)["normal"].played, 0)

    def test_totals_add_up_across_levels(self):
        for _ in range(2):
            make_win(self.ana, "facil", seconds_remaining=10, moves_remaining=10)
        for _ in range(2):
            make_lost(self.ana, "facil", Game.TIMEOUT)
        make_win(self.ana, "normal", seconds_remaining=10, moves_remaining=10)
        for _ in range(2):
            make_lost(self.ana, "normal", Game.ABANDONED)

        totals = player_stats(self.ana).totals

        self.assertEqual((totals.played, totals.won, totals.lost), (7, 3, 4))
        self.assertEqual(totals.win_percent, 43)

    def test_a_user_with_no_attempts_has_zero_counts_everywhere(self):
        stats = player_stats(self.ana)

        for row in [*stats.levels, stats.totals]:
            self.assertEqual((row.played, row.won, row.lost), (0, 0, 0), row.name)
            self.assertIsNone(row.win_percent, row.name)


class PlayerStatsTimeAndBestTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")

    def rows(self, stats):
        return {row.slug: row for row in stats.levels}

    def test_average_covers_wins_timeouts_and_abandoned_attempts(self):
        make_win(self.ana, "facil", seconds_remaining=80, moves_remaining=200, seconds=100)
        make_lost(self.ana, "facil", Game.TIMEOUT, seconds=180)  # a timeout lasts the level limit
        make_lost(self.ana, "facil", Game.ABANDONED, seconds=20)

        easy = self.rows(player_stats(self.ana))["facil"]

        self.assertEqual(easy.average_seconds, 100)
        self.assertEqual(format_clock(easy.average_seconds), "01:40")

    def test_average_rounds_to_the_nearest_second_half_up(self):
        make_win(self.ana, "facil", seconds_remaining=80, moves_remaining=200, seconds=100)
        make_win(self.ana, "facil", seconds_remaining=79, moves_remaining=200, seconds=101)

        easy = self.rows(player_stats(self.ana))["facil"]

        self.assertEqual(format_clock(easy.average_seconds), "01:41")

    def test_totals_average_is_total_duration_over_total_played_not_a_mean_of_means(self):
        make_lost(self.ana, "facil", Game.ABANDONED, seconds=60)
        make_lost(self.ana, "facil", Game.ABANDONED, seconds=60)
        make_lost(self.ana, "normal", Game.ABANDONED, seconds=180)

        stats = player_stats(self.ana)

        self.assertEqual(self.rows(stats)["facil"].average_seconds, 60)
        self.assertEqual(self.rows(stats)["normal"].average_seconds, 180)
        self.assertEqual(stats.totals.average_seconds, 100)  # a mean of means would give 120

    def test_an_active_attempt_does_not_enter_the_average(self):
        make_lost(self.ana, "facil", Game.ABANDONED, seconds=40)
        make_game(self.ana, "facil", started_at=at(-30))

        easy = self.rows(player_stats(self.ana))["facil"]

        self.assertEqual(easy.average_seconds, 40)

    def test_no_games_means_no_percentage_best_or_average(self):
        stats = player_stats(self.ana)

        for row in [*stats.levels, stats.totals]:
            self.assertIsNone(row.win_percent, row.name)
            self.assertIsNone(row.best, row.name)
            self.assertIsNone(row.average_seconds, row.name)

    def test_best_is_the_highest_win_of_the_level(self):
        make_win(self.ana, "facil", seconds_remaining=60, moves_remaining=140)  # 200
        make_win(self.ana, "facil", seconds_remaining=80, moves_remaining=260)  # 340
        make_win(self.ana, "facil", seconds_remaining=50, moves_remaining=260)  # 310

        self.assertEqual(self.rows(player_stats(self.ana))["facil"].best, 340)

    def test_losses_never_create_a_best(self):
        make_lost(self.ana, "facil", Game.TIMEOUT)
        make_lost(self.ana, "normal", Game.MOVE_LIMIT)
        make_lost(self.ana, "dificil", Game.ABANDONED)

        stats = player_stats(self.ana)

        for row in [*stats.levels, stats.totals]:
            self.assertIsNone(row.best, row.name)

    def test_totals_best_is_the_sum_of_the_level_bests_when_a_level_has_no_win(self):
        make_win(self.ana, "facil", seconds_remaining=120, moves_remaining=85)  # 205
        make_win(self.ana, "normal", seconds_remaining=300, moves_remaining=200)  # 1000

        stats = player_stats(self.ana)

        self.assertEqual([row.best for row in stats.levels], [205, 1000, None])
        self.assertEqual(stats.totals.best, 1205)

    def test_totals_best_is_the_sum_over_all_three_levels(self):
        make_win(self.ana, "facil", seconds_remaining=120, moves_remaining=85)  # 205
        make_win(self.ana, "normal", seconds_remaining=300, moves_remaining=200)  # 1000
        make_win(self.ana, "dificil", seconds_remaining=500, moves_remaining=300)  # 2400

        stats = player_stats(self.ana)

        self.assertEqual([row.best for row in stats.levels], [205, 1000, 2400])
        self.assertEqual(stats.totals.best, 3605)

    def test_totals_best_with_a_single_win_is_that_score(self):
        make_win(self.ana, "facil", seconds_remaining=80, moves_remaining=260)

        self.assertEqual(player_stats(self.ana).totals.best, 340)

    def test_totals_best_is_none_when_there_is_no_win_anywhere(self):
        make_lost(self.ana, "facil", Game.TIMEOUT)

        self.assertIsNone(player_stats(self.ana).totals.best)


class PlayerStatsQueryCountTests(TestCase):
    def test_two_queries_for_a_user_with_no_attempts(self):
        ana = make_user("ana")

        with self.assertNumQueries(2):  # levels + one aggregate
            player_stats(ana)

    def test_the_same_two_queries_for_a_user_with_many_attempts(self):
        ana = make_user("ana")
        for slug in ("facil", "normal", "dificil"):
            for _ in range(4):
                make_win(ana, slug, seconds_remaining=10, moves_remaining=10)
                make_lost(ana, slug, Game.TIMEOUT)
        make_game(ana, "facil")

        with self.assertNumQueries(2):
            player_stats(ana)
