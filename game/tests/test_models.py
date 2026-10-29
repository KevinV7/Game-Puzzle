from datetime import datetime, timedelta, timezone

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from game.models import Game, Level, Move

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
BOARD = [1, 2, 3, 4, 5, 6, 7, 0, 8]


def make_user(name="ana"):
    return get_user_model().objects.create_user(name, f"{name}@example.com", "x")


def make_game(user, level, **fields):
    """Insert a Game directly, bypassing the service layer."""
    values = {
        "status": Game.ACTIVE,
        "initial_board": BOARD,
        "board": BOARD,
        "started_at": T0,
        "deadline": T0 + timedelta(seconds=level.time_limit_seconds),
    }
    values.update(fields)
    return Game.objects.create(user=user, level=level, **values)


class LevelCatalogTests(TestCase):
    def test_migrations_seed_the_three_levels_in_progression_order(self):
        rows = list(
            Level.objects.values_list(
                "slug", "name", "order", "size", "time_limit_seconds", "move_limit", "multiplier"
            )
        )
        self.assertEqual(
            rows,
            [
                ("facil", "Fácil", 1, 3, 180, 300, 1),
                ("normal", "Normal", 2, 4, 420, 700, 2),
                ("dificil", "Difícil", 3, 5, 720, 1500, 3),
            ],
        )

    def test_deleting_a_level_with_games_is_blocked(self):
        make_game(make_user(), Level.objects.get(slug="facil"))
        with self.assertRaises(ProtectedError):
            Level.objects.get(slug="facil").delete()


class ActiveGameConstraintTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.easy = Level.objects.get(slug="facil")

    def test_a_second_active_game_for_the_same_user_is_rejected(self):
        make_game(self.user, self.easy)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_game(self.user, Level.objects.get(slug="normal"))

    def test_another_user_may_have_an_active_game(self):
        make_game(self.user, self.easy)
        make_game(make_user("beto"), self.easy)
        self.assertEqual(Game.objects.filter(status=Game.ACTIVE).count(), 2)

    def test_finished_games_accumulate_next_to_an_active_one(self):
        for _ in range(2):
            make_game(
                self.user,
                self.easy,
                status=Game.LOST,
                loss_reason=Game.TIMEOUT,
                ended_at=T0 + timedelta(seconds=180),
            )
        make_game(
            self.user,
            self.easy,
            status=Game.WON,
            ended_at=T0 + timedelta(seconds=50),
            seconds_remaining=130,
            moves_remaining=250,
        )
        make_game(self.user, self.easy)
        self.assertEqual(Game.objects.filter(user=self.user).count(), 4)


class GameConsistencyConstraintTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.easy = Level.objects.get(slug="facil")
        self.end = T0 + timedelta(seconds=60)

    def assertRejected(self, **fields):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_game(self.user, self.easy, **fields)

    def test_lost_without_a_reason_is_rejected(self):
        self.assertRejected(status=Game.LOST, ended_at=self.end)

    def test_won_with_a_loss_reason_is_rejected(self):
        self.assertRejected(
            status=Game.WON,
            loss_reason=Game.TIMEOUT,
            ended_at=self.end,
            seconds_remaining=1,
            moves_remaining=1,
        )

    def test_won_without_the_remaining_fields_is_rejected(self):
        self.assertRejected(status=Game.WON, ended_at=self.end)
        self.assertRejected(status=Game.WON, ended_at=self.end, seconds_remaining=1)
        self.assertRejected(status=Game.WON, ended_at=self.end, moves_remaining=1)

    def test_active_with_an_end_time_or_a_reason_is_rejected(self):
        self.assertRejected(ended_at=self.end)
        self.assertRejected(loss_reason=Game.TIMEOUT)

    def test_finished_games_need_an_end_time(self):
        self.assertRejected(status=Game.LOST, loss_reason=Game.MOVE_LIMIT)
        self.assertRejected(status=Game.WON, seconds_remaining=1, moves_remaining=1)

    def test_remaining_fields_belong_to_won_games_only(self):
        self.assertRejected(
            status=Game.LOST,
            loss_reason=Game.TIMEOUT,
            ended_at=self.end,
            seconds_remaining=1,
            moves_remaining=1,
        )

    def test_every_loss_reason_is_accepted_on_a_lost_game(self):
        for reason in (Game.TIMEOUT, Game.MOVE_LIMIT, Game.ABANDONED):
            with self.subTest(reason):
                make_game(self.user, self.easy, status=Game.LOST, loss_reason=reason, ended_at=self.end)


class GameBehaviourTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.easy = Level.objects.get(slug="facil")

    def test_duration_is_end_minus_start(self):
        game = make_game(
            self.user,
            self.easy,
            status=Game.LOST,
            loss_reason=Game.TIMEOUT,
            ended_at=T0 + timedelta(seconds=180),
        )
        self.assertEqual(game.duration, timedelta(seconds=180))

    def test_duration_is_none_while_active(self):
        self.assertIsNone(make_game(self.user, self.easy).duration)

    def test_move_sequence_is_unique_per_game(self):
        game = make_game(self.user, self.easy)
        Move.objects.create(game=game, seq=1, tile=8, valid=True)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Move.objects.create(game=game, seq=1, tile=5, valid=False)

    def test_the_same_sequence_is_allowed_in_another_game(self):
        first = make_game(self.user, self.easy, status=Game.LOST, loss_reason=Game.TIMEOUT, ended_at=T0)
        second = make_game(self.user, self.easy)
        Move.objects.create(game=first, seq=1, tile=8, valid=True)
        Move.objects.create(game=second, seq=1, tile=8, valid=True)
        self.assertEqual(Move.objects.count(), 2)


class AdminTests(TestCase):
    def test_game_move_and_level_are_read_only_in_the_admin(self):
        request = RequestFactory().get("/admin/")
        request.user = get_user_model().objects.create_superuser("root", "root@example.com", "x")
        for model in (Game, Move, Level):
            model_admin = admin.site._registry[model]
            with self.subTest(model.__name__):
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_change_permission(request))
                self.assertFalse(model_admin.has_delete_permission(request))

    def test_the_add_page_for_game_is_forbidden_even_for_a_superuser(self):
        self.client.force_login(get_user_model().objects.create_superuser("root", "root@example.com", "x"))
        self.assertEqual(self.client.get(reverse("admin:game_game_add")).status_code, 403)
