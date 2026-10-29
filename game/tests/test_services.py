from unittest import mock

from django.test import TestCase

from game import engine, services
from game.models import Game, Level
from game.services import ConfirmRequired, LevelLocked
from game.tests.helpers import (
    T0,
    at,
    make_completed_user,
    make_game,
    make_lost,
    make_user,
    make_user_who_won,
    make_won,
    seeded_rng,
)


def level(slug):
    return Level.objects.get(slug=slug)


def active_games(user):
    return Game.objects.filter(user=user, status=Game.ACTIVE)


class ProgressionTests(TestCase):
    def flags(self, user):
        catalog = services.levels_for(user, now=at(0))
        return {
            entry.level.slug: (entry.playable, entry.passed, entry.current)
            for entry in catalog.levels
        }

    def test_new_user_can_only_play_easy(self):
        user = make_user()
        self.assertEqual(
            self.flags(user),
            {
                "facil": (True, False, True),
                "normal": (False, False, False),
                "dificil": (False, False, False),
            },
        )
        catalog = services.levels_for(user, now=at(0))
        self.assertFalse(catalog.completed)
        self.assertEqual(catalog.current_level.slug, "facil")
        self.assertIsNone(catalog.active_game)

    def test_catalog_lists_levels_in_progression_order(self):
        catalog = services.levels_for(make_user(), now=at(0))
        self.assertEqual([entry.level.slug for entry in catalog.levels], ["facil", "normal", "dificil"])

    def test_easy_win_makes_normal_the_only_playable_level(self):
        user = make_user_who_won(["facil"])
        self.assertEqual(
            self.flags(user),
            {
                "facil": (False, True, False),
                "normal": (True, False, True),
                "dificil": (False, False, False),
            },
        )

    def test_normal_win_makes_hard_the_only_playable_level(self):
        user = make_user_who_won(["facil", "normal"])
        playable = [slug for slug, (can_play, _, _) in self.flags(user).items() if can_play]
        self.assertEqual(playable, ["dificil"])

    def test_hard_win_completes_the_game_and_unlocks_everything(self):
        user = make_completed_user()
        self.assertEqual(
            self.flags(user),
            {slug: (True, True, False) for slug in ("facil", "normal", "dificil")},
        )
        catalog = services.levels_for(user, now=at(0))
        self.assertTrue(catalog.completed)
        self.assertIsNone(catalog.current_level)

    def test_losing_a_replay_keeps_everything_available(self):
        user = make_completed_user()
        make_lost(user, "facil", Game.MOVE_LIMIT)
        make_lost(user, "dificil", Game.TIMEOUT)
        self.assertTrue(services.completed(user))
        self.assertTrue(all(playable for playable, _, _ in self.flags(user).values()))

    def test_other_users_wins_do_not_unlock(self):
        make_user_who_won(["facil", "normal"], name="beto")
        ana = make_user("ana")
        self.assertEqual(services.won_orders(ana), set())
        self.assertFalse(services.playable(ana, level("normal")))

    def test_helpers_follow_the_derived_rules(self):
        user = make_user_who_won(["facil"])
        self.assertEqual(services.won_orders(user), {1})
        self.assertFalse(services.completed(user))
        self.assertEqual(services.current_order(user), 2)
        self.assertTrue(services.playable(user, level("normal")))
        self.assertFalse(services.playable(user, level("facil")))
        self.assertFalse(services.playable(user, level("dificil")))

    def test_current_order_is_none_once_completed(self):
        self.assertIsNone(services.current_order(make_completed_user()))

    def test_catalog_reports_the_active_game_on_its_level(self):
        user = make_user()
        game = make_game(user, "facil", started_at=at(0))
        catalog = services.levels_for(user, now=at(10))
        self.assertEqual(catalog.active_game, game)
        self.assertEqual([entry.active_game for entry in catalog.levels], [game, None, None])

    def test_catalog_of_an_expired_attempt_finalizes_without_restarting(self):
        user = make_user()
        game = make_game(user, "facil", started_at=at(0))
        catalog = services.levels_for(user, now=at(3600))
        self.assertIsNone(catalog.active_game)
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason), (Game.LOST, Game.TIMEOUT))
        self.assertEqual(game.ended_at, game.deadline)
        self.assertEqual(Game.objects.filter(user=user).count(), 1)
        self.assertEqual(active_games(user).count(), 0)
        services.levels_for(user, now=at(7200))
        self.assertEqual(Game.objects.filter(user=user, loss_reason=Game.TIMEOUT).count(), 1)


class StartTests(TestCase):
    def test_first_start_creates_a_solvable_unsolved_easy_attempt(self):
        user = make_user()
        game = services.start(user, level("facil"), now=at(5), rng=seeded_rng(3))
        self.assertEqual(game.level.slug, "facil")
        self.assertEqual(game.status, Game.ACTIVE)
        self.assertEqual(game.loss_reason, "")
        self.assertEqual(game.moves_used, 0)
        self.assertEqual(game.started_at, at(5))
        self.assertEqual(game.deadline, at(5 + 180))
        self.assertIsNone(game.ended_at)
        board = tuple(game.board)
        self.assertEqual(len(board), 9)
        self.assertFalse(engine.is_solved(board))
        self.assertTrue(engine.is_solvable(board))
        self.assertEqual(game.initial_board, game.board)
        self.assertEqual(board, engine.shuffle(3, seeded_rng(3)))
        self.assertEqual(game.moves.count(), 0)

    def test_starting_the_same_level_returns_the_same_attempt_unchanged(self):
        user = make_user()
        game = make_game(user, "facil", board=[1, 2, 3, 4, 0, 6, 7, 5, 8], moves_used=10, started_at=at(0))
        again = services.start(user, level("facil"), now=at(60), rng=seeded_rng())
        self.assertEqual(again.pk, game.pk)
        game.refresh_from_db()
        self.assertEqual(game.board, [1, 2, 3, 4, 0, 6, 7, 5, 8])
        self.assertEqual((game.moves_used, game.started_at), (10, at(0)))
        self.assertEqual(Game.objects.filter(user=user).count(), 1)

    def test_a_new_user_cannot_start_normal_or_hard(self):
        user = make_user()
        for slug in ("normal", "dificil"):
            with self.subTest(slug), self.assertRaises(LevelLocked):
                services.start(user, level(slug), now=at(0))
        self.assertEqual(Game.objects.count(), 0)

    def test_hard_needs_normal(self):
        user = make_user_who_won(["facil"])
        with self.assertRaises(LevelLocked):
            services.start(user, level("dificil"), now=at(0))
        self.assertEqual(Game.objects.filter(user=user).count(), 1)

    def test_no_going_back_before_completion(self):
        user = make_user_who_won(["facil"])
        with self.assertRaises(LevelLocked):
            services.start(user, level("facil"), now=at(0))
        self.assertEqual(Game.objects.filter(user=user).count(), 1)

    def test_no_second_level_while_an_attempt_is_active(self):
        user = make_user_who_won(["facil"])
        active = make_game(user, "normal", started_at=at(0))
        for slug in ("facil", "dificil"):
            with self.subTest(slug), self.assertRaises(LevelLocked):
                services.start(user, level(slug), confirm=True, now=at(10))
        active.refresh_from_db()
        self.assertEqual(active.status, Game.ACTIVE)
        self.assertFalse(Game.objects.filter(loss_reason=Game.ABANDONED).exists())

    def test_other_users_wins_do_not_unlock_a_start(self):
        make_user_who_won(["facil"], name="beto")
        with self.assertRaises(LevelLocked):
            services.start(make_user("ana"), level("normal"), now=at(0))

    def test_finished_attempts_accumulate_when_a_new_one_starts(self):
        user = make_user()
        lost = make_lost(user, "facil", Game.TIMEOUT)
        game = services.start(user, level("facil"), now=at(1000), rng=seeded_rng())
        self.assertEqual(Game.objects.filter(user=user).count(), 2)
        lost.refresh_from_db()
        self.assertEqual((lost.status, lost.loss_reason), (Game.LOST, Game.TIMEOUT))
        self.assertEqual(game.status, Game.ACTIVE)

    def test_the_next_level_starts_only_when_requested(self):
        user = make_user_who_won(["facil"])
        self.assertEqual(active_games(user).count(), 0)
        game = services.start(user, level("normal"), now=at(0), rng=seeded_rng())
        self.assertEqual((game.level.slug, len(game.board)), ("normal", 16))
        self.assertEqual(game.deadline, at(420))


class CompletedUserSwitchTests(TestCase):
    def setUp(self):
        self.user = make_completed_user()

    def test_switching_without_confirmation_changes_nothing(self):
        game = make_game(self.user, "facil", moves_used=4, started_at=at(0))
        with self.assertRaises(ConfirmRequired):
            services.start(self.user, level("normal"), now=at(10))
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason, game.moves_used), (Game.ACTIVE, "", 4))
        self.assertFalse(Game.objects.filter(user=self.user, level__slug="normal", status=Game.ACTIVE).exists())
        self.assertFalse(Game.objects.filter(loss_reason=Game.ABANDONED).exists())

    def test_confirmed_switch_abandons_the_old_attempt_and_starts_the_new_one(self):
        old = make_game(self.user, "facil", started_at=at(0))
        new = services.start(self.user, level("normal"), confirm=True, now=at(30), rng=seeded_rng())
        old.refresh_from_db()
        self.assertEqual((old.status, old.loss_reason, old.ended_at), (Game.LOST, Game.ABANDONED, at(30)))
        self.assertEqual((new.level.slug, new.status, new.started_at), ("normal", Game.ACTIVE, at(30)))
        self.assertEqual(list(active_games(self.user)), [new])
        # The abandoned attempt gets no automatic same-level replacement.
        self.assertEqual(Game.objects.filter(user=self.user, level__slug="facil", status=Game.ACTIVE).count(), 0)

    def test_a_failed_confirmed_switch_leaves_the_old_attempt_active(self):
        old = make_game(self.user, "facil", started_at=at(0))
        with mock.patch("game.services.engine.shuffle", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                services.start(self.user, level("normal"), confirm=True, now=at(30))
        old.refresh_from_db()
        self.assertEqual((old.status, old.loss_reason, old.ended_at), (Game.ACTIVE, "", None))
        self.assertEqual(list(active_games(self.user)), [old])

    def test_switch_after_expiry_records_only_the_timeout(self):
        for confirm in (False, True):
            with self.subTest(confirm=confirm):
                Game.objects.filter(user=self.user, status=Game.ACTIVE).delete()
                old = make_game(self.user, "facil", started_at=at(0))
                new = services.start(self.user, level("normal"), confirm=confirm, now=at(500), rng=seeded_rng())
                old.refresh_from_db()
                self.assertEqual((old.status, old.loss_reason, old.ended_at), (Game.LOST, Game.TIMEOUT, at(180)))
                self.assertEqual(list(active_games(self.user)), [new])
                self.assertEqual(Game.objects.filter(user=self.user, level__slug="facil", status=Game.ACTIVE).count(), 0)
                self.assertFalse(Game.objects.filter(loss_reason=Game.ABANDONED).exists())
                new.delete()

    def test_starting_the_same_level_again_returns_it_unchanged(self):
        game = make_game(self.user, "normal", started_at=at(0), moves_used=2)
        again = services.start(self.user, level("normal"), now=at(5))
        self.assertEqual(again.pk, game.pk)
        self.assertFalse(Game.objects.filter(loss_reason=Game.ABANDONED).exists())

    def test_a_completed_user_may_start_a_replayed_level_without_an_active_attempt(self):
        game = services.start(self.user, level("facil"), now=T0, rng=seeded_rng())
        self.assertEqual(game.level.slug, "facil")
