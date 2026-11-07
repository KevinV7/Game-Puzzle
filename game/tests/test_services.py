from dataclasses import fields as dataclass_fields
from datetime import timedelta
from unittest import mock

from django.test import TestCase

from game import engine, services
from game.models import Game, Level, Move
from game.services import ConfirmRequired, LevelLocked, Stale
from game.tests.helpers import (
    T0,
    almost_solved,
    at,
    make_completed_user,
    make_game,
    make_lost,
    make_user,
    make_user_who_won,
    make_won,
    seeded_rng,
    winning_tile,
)

# Gap in the centre. Tile 5 slides in and lands in place; tile 2 slides in and does
# not; tile 1 is not next to the gap.
CENTRE_GAP = [1, 2, 3, 4, 0, 6, 7, 5, 8]


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


class MoveTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def move(self, game, tile, *, seq=None, now=None, seed=5):
        seq = game.moves_used + 1 if seq is None else seq
        return services.move(self.user, game.pk, tile, seq, now=now or at(10), rng=seeded_rng(seed))

    def active_count(self):
        return active_games(self.user).count()

    def test_valid_move_swaps_the_tile_into_the_gap_and_is_logged(self):
        game = make_game(self.user, board=CENTRE_GAP)
        outcome = self.move(game, 5, now=at(10))
        game.refresh_from_db()
        self.assertEqual(game.board, [1, 2, 3, 4, 5, 6, 7, 0, 8])
        self.assertEqual((game.moves_used, game.status), (1, Game.ACTIVE))
        entry = game.moves.get()
        self.assertEqual((entry.seq, entry.tile, entry.valid, entry.created_at), (1, 5, True, at(10)))
        self.assertTrue(outcome.move.valid)
        self.assertTrue(outcome.tile_in_place)
        self.assertEqual(outcome.game.pk, game.pk)
        self.assertIsNone(outcome.ended)

    def test_a_valid_move_into_the_wrong_cell_is_not_in_place(self):
        outcome = self.move(make_game(self.user, board=CENTRE_GAP), 2)
        self.assertTrue(outcome.move.valid)
        self.assertFalse(outcome.tile_in_place)

    def test_invalid_move_is_counted_and_logged_and_leaves_the_board(self):
        game = make_game(self.user, board=CENTRE_GAP)
        outcome = self.move(game, 1)
        game.refresh_from_db()
        self.assertEqual((game.board, game.moves_used, game.status), (CENTRE_GAP, 1, Game.ACTIVE))
        self.assertEqual(list(game.moves.values_list("seq", "tile", "valid")), [(1, 1, False)])
        self.assertFalse(outcome.move.valid)
        self.assertFalse(outcome.tile_in_place)

    def test_a_tile_that_is_not_on_the_board_is_rejected_without_counting(self):
        game = make_game(self.user, board=CENTRE_GAP)
        for tile in (99, 0, -1):
            with self.subTest(tile), self.assertRaises(ValueError):
                self.move(game, tile)
        game.refresh_from_db()
        self.assertEqual(game.moves_used, 0)
        self.assertEqual(Move.objects.count(), 0)

    def test_ordinary_win(self):
        game = make_game(self.user, moves_used=20, started_at=at(0))
        outcome = self.move(game, winning_tile(3), now=at(40))
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason, game.moves_used), (Game.WON, "", 21))
        self.assertEqual((game.moves_remaining, game.seconds_remaining), (279, 140))
        self.assertEqual(game.ended_at, at(40))
        self.assertEqual(outcome.game.pk, game.pk)
        self.assertIsNone(outcome.ended)
        self.assertEqual(self.active_count(), 0)
        self.assertEqual(Game.objects.filter(user=self.user).count(), 1)

    def test_win_on_the_last_allowed_move_has_no_moves_remaining(self):
        game = make_game(self.user, moves_used=299)
        self.move(game, winning_tile(3))
        game.refresh_from_db()
        self.assertEqual((game.status, game.moves_used, game.moves_remaining), (Game.WON, 300, 0))

    def test_win_one_second_before_the_deadline(self):
        game = make_game(self.user, started_at=at(0))
        self.move(game, winning_tile(3), now=at(179))
        game.refresh_from_db()
        self.assertEqual((game.status, game.seconds_remaining), (Game.WON, 1))

    def test_win_rounds_the_remaining_seconds_down(self):
        game = make_game(self.user, started_at=at(0))
        self.move(game, winning_tile(3), now=at(100.4))
        game.refresh_from_db()
        self.assertEqual(game.seconds_remaining, 79)

    def test_invalid_move_cannot_win_even_one_move_from_solved(self):
        game = make_game(self.user)
        self.move(game, 1)
        game.refresh_from_db()
        self.assertEqual(game.status, Game.ACTIVE)

    def test_move_limit_reached_by_a_valid_move_loses_and_starts_a_replacement(self):
        game = make_game(self.user, board=CENTRE_GAP, moves_used=299, started_at=at(0))
        outcome = self.move(game, 5, now=at(50), seed=5)
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason, game.moves_used), (Game.LOST, Game.MOVE_LIMIT, 300))
        self.assertEqual(game.ended_at, at(50))
        self.assertEqual(outcome.ended.pk, game.pk)
        fresh = outcome.game
        self.assertNotEqual(fresh.pk, game.pk)
        self.assertEqual((fresh.status, fresh.moves_used, fresh.level_id), (Game.ACTIVE, 0, game.level_id))
        self.assertEqual((fresh.started_at, fresh.deadline), (at(50), at(230)))
        self.assertEqual(tuple(fresh.board), engine.shuffle(3, seeded_rng(5)))
        self.assertEqual(self.active_count(), 1)

    def test_move_limit_reached_by_an_invalid_move_loses_too(self):
        game = make_game(self.user, board=CENTRE_GAP, moves_used=299)
        outcome = self.move(game, 1)
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason, game.moves_used), (Game.LOST, Game.MOVE_LIMIT, 300))
        self.assertFalse(game.moves.get().valid)
        self.assertEqual(outcome.game.moves_used, 0)
        self.assertEqual(self.active_count(), 1)

    def test_move_after_the_deadline_is_not_applied_and_times_the_game_out(self):
        game = make_game(self.user, started_at=at(0))
        outcome = self.move(game, winning_tile(3), now=at(181))
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason, game.moves_used), (Game.LOST, Game.TIMEOUT, 0))
        self.assertEqual(game.ended_at, at(180))
        self.assertEqual(game.board, almost_solved(3))
        self.assertEqual(Move.objects.count(), 0)
        self.assertIsNone(outcome.move)
        self.assertEqual(outcome.ended.pk, game.pk)
        self.assertEqual((outcome.game.started_at, outcome.game.deadline), (at(181), at(361)))
        self.assertEqual(self.active_count(), 1)
        self.assertFalse(Game.objects.filter(status=Game.WON).exists())

    def test_a_winning_move_exactly_at_the_deadline_is_not_applied(self):
        game = make_game(self.user, started_at=at(0))
        self.move(game, winning_tile(3), now=at(180))
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason), (Game.LOST, Game.TIMEOUT))
        self.assertFalse(Game.objects.filter(status=Game.WON).exists())

    def test_a_stale_sequence_number_is_refused_and_changes_nothing(self):
        game = make_game(self.user, board=CENTRE_GAP, moves_used=5)
        for seq in (0, 3, 5, 7):
            with self.subTest(seq), self.assertRaises(Stale):
                self.move(game, 5, seq=seq)
        game.refresh_from_db()
        self.assertEqual((game.board, game.moves_used), (CENTRE_GAP, 5))
        self.assertEqual(Move.objects.count(), 0)

    def test_the_same_move_sent_twice_is_applied_once(self):
        game = make_game(self.user, board=CENTRE_GAP, moves_used=5)
        self.move(game, 5, seq=6)
        with self.assertRaises(Stale):
            self.move(game, 5, seq=6)
        game.refresh_from_db()
        self.assertEqual(game.moves_used, 6)

    def test_moves_on_a_finished_game_are_refused(self):
        won = make_won(self.user, "facil")
        with self.assertRaises(Stale):
            self.move(won, winning_tile(3), seq=1)
        lost = make_lost(self.user, "facil", Game.TIMEOUT)
        with self.assertRaises(Stale):
            self.move(lost, winning_tile(3), seq=1)
        lost.refresh_from_db()
        self.assertEqual((lost.status, lost.moves_used), (Game.LOST, 0))
        self.assertEqual(Move.objects.count(), 0)

    def test_a_foreign_or_unknown_game_does_not_exist(self):
        other = make_game(make_user("beto"))
        for game_id in (other.pk, other.pk + 1000):
            with self.subTest(game_id):
                with self.assertRaises(Game.DoesNotExist):
                    services.move(self.user, game_id, winning_tile(3), 1, now=at(10))
                with self.assertRaises(Game.DoesNotExist):
                    services.state(self.user, game_id, now=at(10))
        other.refresh_from_db()
        self.assertEqual((other.status, other.moves_used), (Game.ACTIVE, 0))

    def test_a_loss_leaves_exactly_one_active_game_for_the_user(self):
        game = make_game(self.user, board=CENTRE_GAP, moves_used=299)
        self.move(game, 1)
        self.assertEqual(self.active_count(), 1)


class StateAndExpiryTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_state_of_a_running_game_returns_it_and_the_remaining_time(self):
        game = make_game(self.user, moves_used=12, started_at=at(0))
        outcome = services.state(self.user, game.pk, now=at(60))
        self.assertEqual(outcome.game.pk, game.pk)
        self.assertIsNone(outcome.ended)
        self.assertEqual(services.remaining_seconds(outcome.game, at(60)), 120)
        self.assertEqual(outcome.game.moves_used, 12)

    def test_state_after_a_long_absence_records_one_timeout_and_one_replacement(self):
        game = make_game(self.user, started_at=at(0))
        outcome = services.state(self.user, game.pk, now=at(3600), rng=seeded_rng())
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason, game.ended_at), (Game.LOST, Game.TIMEOUT, at(180)))
        self.assertEqual(outcome.ended.pk, game.pk)
        fresh = outcome.game
        self.assertEqual((fresh.level_id, fresh.status, fresh.started_at), (game.level_id, Game.ACTIVE, at(3600)))
        self.assertEqual(services.remaining_seconds(fresh, at(3600)), 180)
        self.assertEqual(active_games(self.user).count(), 1)

    def test_asking_again_for_the_expired_game_does_not_record_a_second_loss(self):
        game = make_game(self.user, started_at=at(0))
        services.state(self.user, game.pk, now=at(3600), rng=seeded_rng())
        again = services.state(self.user, game.pk, now=at(3700), rng=seeded_rng())
        self.assertEqual(again.game.pk, game.pk)
        self.assertIsNone(again.ended)
        self.assertEqual(Game.objects.filter(user=self.user, loss_reason=Game.TIMEOUT).count(), 1)
        self.assertEqual(active_games(self.user).count(), 1)

    def test_state_exactly_at_the_deadline_is_a_timeout(self):
        game = make_game(self.user, started_at=at(0))
        outcome = services.state(self.user, game.pk, now=at(180), rng=seeded_rng())
        self.assertEqual(outcome.ended.pk, game.pk)

    def test_finalize_expired_twice_records_one_loss(self):
        game = make_game(self.user, started_at=at(0))
        self.assertEqual(services.finalize_expired(self.user, now=at(200)).pk, game.pk)
        self.assertIsNone(services.finalize_expired(self.user, now=at(300)))
        self.assertEqual(Game.objects.filter(user=self.user, loss_reason=Game.TIMEOUT).count(), 1)
        self.assertEqual(active_games(self.user).count(), 0)

    def test_finalize_expired_leaves_a_running_game_alone(self):
        game = make_game(self.user, started_at=at(0))
        self.assertIsNone(services.finalize_expired(self.user, now=at(179)))
        game.refresh_from_db()
        self.assertEqual(game.status, Game.ACTIVE)

    def test_remaining_seconds_floors_and_is_never_negative(self):
        game = make_game(self.user, started_at=at(0))
        for seconds, expected in [(0, 180), (100.4, 79), (179.999, 0), (180, 0), (500, 0)]:
            with self.subTest(seconds):
                self.assertEqual(services.remaining_seconds(game, at(seconds)), expected)


class OutcomeDataTests(TestCase):
    def win(self, user, slug):
        game = make_game(user, slug, started_at=at(0))
        size = game.level.size
        return services.move(user, game.pk, winning_tile(size), 1, now=at(10))

    def test_easy_and_normal_wins_of_a_new_user_point_to_the_next_level(self):
        user = make_user()
        outcome = self.win(user, "facil")
        self.assertEqual(outcome.next_level.slug, "normal")
        self.assertFalse(outcome.completed_all)
        self.assertFalse(outcome.can_switch)
        outcome = self.win(user, "normal")
        self.assertEqual(outcome.next_level.slug, "dificil")
        self.assertFalse(outcome.completed_all)
        self.assertFalse(outcome.can_switch)

    def test_the_hard_win_that_completes_the_game(self):
        outcome = self.win(make_user_who_won(["facil", "normal"]), "dificil")
        self.assertIsNone(outcome.next_level)
        self.assertTrue(outcome.completed_all)
        self.assertTrue(outcome.can_switch)

    def test_wins_of_a_completed_user_set_neither_flag(self):
        user = make_completed_user()
        for slug in ("facil", "dificil"):
            with self.subTest(slug):
                outcome = self.win(user, slug)
                self.assertIsNone(outcome.next_level)
                self.assertFalse(outcome.completed_all)
                self.assertTrue(outcome.can_switch)

    def test_non_winning_outcomes_carry_no_progression_but_report_can_switch(self):
        fresh = make_game(make_user("beto"), board=CENTRE_GAP)
        outcome = services.move(fresh.user, fresh.pk, 5, 1, now=at(10))
        self.assertEqual((outcome.next_level, outcome.completed_all, outcome.can_switch), (None, False, False))
        done = make_completed_user("cris")
        game = make_game(done, "facil", board=CENTRE_GAP)
        outcome = services.move(done, game.pk, 5, 1, now=at(10))
        self.assertEqual((outcome.next_level, outcome.completed_all, outcome.can_switch), (None, False, True))


class DataContractTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_won_attempt_exposes_remaining_values_and_duration(self):
        game = make_game(self.user, moves_used=39, started_at=at(0))
        services.move(self.user, game.pk, winning_tile(3), 40, now=at(100))
        game.refresh_from_db()
        self.assertEqual(game.level.slug, "facil")
        self.assertEqual((game.status, game.seconds_remaining, game.moves_remaining), (Game.WON, 80, 260))
        self.assertEqual(game.duration, timedelta(seconds=100))

    def test_lost_attempts_expose_reason_and_duration(self):
        timeout = make_game(self.user, started_at=at(0))
        services.finalize_expired(self.user, now=at(500))
        timeout.refresh_from_db()
        self.assertEqual((timeout.status, timeout.loss_reason), (Game.LOST, Game.TIMEOUT))
        self.assertEqual(timeout.duration, timedelta(seconds=level("facil").time_limit_seconds))
        limit = make_game(self.user, board=CENTRE_GAP, moves_used=299, started_at=at(1000))
        services.move(self.user, limit.pk, 1, 300, now=at(1070))
        limit.refresh_from_db()
        self.assertEqual((limit.loss_reason, limit.duration), (Game.MOVE_LIMIT, timedelta(seconds=70)))

    def test_abandoned_attempt_exposes_reason_and_duration(self):
        done = make_completed_user("beto")
        old = make_game(done, "facil", started_at=at(2000))
        services.start(done, level("normal"), confirm=True, now=at(2030), rng=seeded_rng())
        old.refresh_from_db()
        self.assertEqual((old.status, old.loss_reason), (Game.LOST, Game.ABANDONED))
        self.assertEqual(old.duration, timedelta(seconds=30))

    def test_values_are_fixed_when_the_attempt_finishes(self):
        game = make_game(self.user, moves_used=39, started_at=at(0))
        services.move(self.user, game.pk, winning_tile(3), 40, now=at(100))
        game.refresh_from_db()
        before = (game.seconds_remaining, game.moves_remaining, game.ended_at)
        services.levels_for(self.user, now=at(100 + 3600))
        services.state(self.user, game.pk, now=at(100 + 3600))
        services.finalize_expired(self.user, now=at(100 + 3600))
        game.refresh_from_db()
        self.assertEqual((game.seconds_remaining, game.moves_remaining, game.ended_at), before)

    def test_no_score_is_stored_or_returned(self):
        stored = {field.name for model in (Game, Move, Level) for field in model._meta.get_fields()}
        self.assertFalse([name for name in stored if "score" in name])
        self.assertFalse([f.name for f in dataclass_fields(services.Outcome) if "score" in f.name])
