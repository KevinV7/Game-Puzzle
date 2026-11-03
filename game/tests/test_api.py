"""JSON API: access control, privacy, move processing and the response contract."""

import json
from datetime import timedelta
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from game.models import Game, Move
from game.tests.helpers import (
    almost_solved,
    make_completed_user,
    make_game,
    make_user,
    make_won,
    winning_tile,
)

# 3x3 board, gap at index 3 (neighbours: tiles 4, 2 and 7), not close to solved.
# Tile 4 belongs in index 3, so moving it lands in place; tile 2 does not.
SCRAMBLED = [4, 1, 3, 0, 2, 6, 7, 5, 8]
RESPONSE_KEYS = {"game", "move", "ended", "next_level", "completed_all", "can_switch", "score"}
GAME_KEYS = {"id", "level", "size", "board", "moves_used", "move_limit", "seconds_remaining", "status", "loss_reason"}


def live_game(user, slug="facil", **fields):
    """An active game whose clock started now, on a scrambled board unless told otherwise."""
    fields.setdefault("board", SCRAMBLED)
    fields.setdefault("started_at", timezone.now())
    return make_game(user, slug, **fields)


def post_move(client, game, tile, seq=None, **extra):
    body = {"tile": tile, "seq": game.moves_used + 1 if seq is None else seq}
    body.update(extra)
    return client.post(reverse("game-move", args=[game.id]), json.dumps(body), content_type="application/json")


class ApiTestCase(TestCase):
    def setUp(self):
        self.ana = make_user("ana")
        self.client.force_login(self.ana)


class AnonymousAccessTests(TestCase):
    def test_anonymous_state_and_move_are_redirected_to_login_without_data(self):
        game = live_game(make_user("beto"))
        for url in [reverse("game-state", args=[game.id]), reverse("game-move", args=[game.id])]:
            with self.subTest(url):
                response = self.client.post(url) if url.endswith("mover/") else self.client.get(url)
                self.assertRedirects(response, f"{reverse('login')}?next={url}", fetch_redirect_response=False)
        game.refresh_from_db()
        self.assertEqual(game.moves_used, 0)


class PrivacyTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.beto_game = live_game(make_user("beto"))

    def assert_not_found_without_data(self, response):
        self.assertEqual(response.status_code, 404)
        content = response.content.decode()
        for leak in ["beto", "board", str(SCRAMBLED)]:
            self.assertNotIn(leak, content)

    def test_state_of_another_users_game_is_404(self):
        self.assert_not_found_without_data(self.client.get(reverse("game-state", args=[self.beto_game.id])))

    def test_move_on_another_users_game_is_404_and_changes_nothing(self):
        self.assert_not_found_without_data(post_move(self.client, self.beto_game, 4))
        self.beto_game.refresh_from_db()
        self.assertEqual(self.beto_game.moves_used, 0)
        self.assertFalse(Move.objects.exists())

    def test_unknown_game_is_404_like_a_foreign_one(self):
        self.assert_not_found_without_data(self.client.get(reverse("game-state", args=[999999])))
        response = self.client.post(
            reverse("game-move", args=[999999]), json.dumps({"tile": 4, "seq": 1}), content_type="application/json"
        )
        self.assert_not_found_without_data(response)


class MethodAndCsrfTests(ApiTestCase):
    def test_wrong_methods_are_405(self):
        game = live_game(self.ana)
        self.assertEqual(self.client.get(reverse("game-move", args=[game.id])).status_code, 405)
        self.assertEqual(self.client.post(reverse("game-state", args=[game.id])).status_code, 405)

    def test_move_without_csrf_token_is_403_and_changes_nothing(self):
        game = live_game(self.ana)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.ana)
        response = post_move(client, game, 4)
        self.assertEqual(response.status_code, 403)
        game.refresh_from_db()
        self.assertEqual(game.moves_used, 0)
        self.assertFalse(Move.objects.exists())


class MalformedRequestTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.game = live_game(self.ana)

    def assert_rejected_and_not_counted(self, response):
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "bad_request")
        self.game.refresh_from_db()
        self.assertEqual(self.game.moves_used, 0)
        self.assertEqual(self.game.board, SCRAMBLED)
        self.assertFalse(Move.objects.exists())

    def test_tiles_that_are_not_on_the_board_are_400(self):
        for tile in [99, 0, 9, -1, "abc", None, 4.5, True, [4]]:
            with self.subTest(tile=tile):
                self.assert_rejected_and_not_counted(post_move(self.client, self.game, tile))

    def test_non_json_bodies_are_400(self):
        url = reverse("game-move", args=[self.game.id])
        for body in ["not json", "", "[1, 2]", "null", '"4"', b"\xff\xfe"]:
            with self.subTest(body=body):
                self.assert_rejected_and_not_counted(self.client.post(url, body, content_type="application/json"))

    def test_missing_tile_or_seq_and_a_direction_body_are_400(self):
        url = reverse("game-move", args=[self.game.id])
        for body in [{"seq": 1}, {"tile": 4}, {"direction": "up", "seq": 1}, {"tile": 4, "seq": "1"}]:
            with self.subTest(body=body):
                response = self.client.post(url, json.dumps(body), content_type="application/json")
                self.assert_rejected_and_not_counted(response)


class MoveResponseTests(ApiTestCase):
    def test_valid_move_response_has_the_documented_shape(self):
        game = live_game(self.ana)
        response = post_move(self.client, game, 4)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), RESPONSE_KEYS)
        self.assertEqual(set(data["game"]), GAME_KEYS)
        self.assertEqual(data["game"]["id"], game.id)
        self.assertEqual(data["game"]["level"], "facil")
        self.assertEqual(data["game"]["size"], 3)
        self.assertEqual(data["game"]["board"], [0, 1, 3, 4, 2, 6, 7, 5, 8])
        self.assertEqual(data["game"]["moves_used"], 1)
        self.assertEqual(data["game"]["move_limit"], 300)
        self.assertEqual(data["game"]["status"], "active")
        self.assertIsNone(data["game"]["loss_reason"])
        self.assertTrue(178 <= data["game"]["seconds_remaining"] <= 180)
        self.assertEqual(data["move"], {"tile": 4, "valid": True, "tile_in_place": True})
        self.assertIsNone(data["ended"])
        self.assertIsNone(data["next_level"])
        self.assertFalse(data["completed_all"])
        self.assertFalse(data["can_switch"])

    def test_tile_in_place_is_false_when_the_tile_lands_elsewhere(self):
        game = live_game(self.ana)
        self.assertEqual(post_move(self.client, game, 2).json()["move"], {"tile": 2, "valid": True, "tile_in_place": False})

    def test_non_adjacent_real_tile_is_an_invalid_counted_move(self):
        game = live_game(self.ana)
        response = post_move(self.client, game, 5)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["move"], {"tile": 5, "valid": False, "tile_in_place": False})
        self.assertEqual(data["game"]["board"], SCRAMBLED)
        self.assertEqual(data["game"]["moves_used"], 1)
        move = Move.objects.get()
        self.assertEqual((move.seq, move.tile, move.valid), (1, 5, False))

    def test_state_response_has_the_same_shape_without_a_move(self):
        game = live_game(self.ana, moves_used=12)
        response = self.client.get(reverse("game-state", args=[game.id]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), RESPONSE_KEYS)
        self.assertEqual(data["game"]["moves_used"], 12)
        self.assertIsNone(data["move"])


class ScorePayloadTests(ApiTestCase):
    def winning_response(self, slug="facil", *, moves_used=20, elapsed=40):
        """Play the winning move of an almost solved board, with the server clock frozen."""
        size = {"facil": 3, "normal": 4, "dificil": 5}[slug]
        now = timezone.now()
        game = live_game(
            self.ana,
            slug,
            board=almost_solved(size),
            moves_used=moves_used,
            started_at=now - timedelta(seconds=elapsed),
        )
        with mock.patch("django.utils.timezone.now", return_value=now):
            return game, post_move(self.client, game, winning_tile(size))

    def test_a_winning_move_carries_the_breakdown_equal_to_the_game_fields(self):
        game, response = self.winning_response()
        data = response.json()
        self.assertEqual(data["game"]["status"], "won")
        # 40 s elapsed of 180 and 20 + 1 moves of 300.
        self.assertEqual(data["score"], {"seconds_remaining": 140, "moves_remaining": 279, "multiplier": 1, "points": 419})
        self.assertEqual(data["score"]["seconds_remaining"], data["game"]["seconds_remaining"])
        self.assertEqual(data["score"]["moves_remaining"], data["game"]["move_limit"] - data["game"]["moves_used"])

    def test_a_normal_win_uses_the_multiplier_of_the_level(self):
        make_won(self.ana, "facil")
        _, response = self.winning_response("normal")
        score = response.json()["score"]
        self.assertEqual(score["multiplier"], 2)
        self.assertEqual(score["points"], (score["seconds_remaining"] + score["moves_remaining"]) * 2)

    def test_the_state_of_a_won_game_returns_the_same_score_now_and_later(self):
        game, response = self.winning_response()
        score = response.json()["score"]
        url = reverse("game-state", args=[game.id])
        self.assertEqual(self.client.get(url).json()["score"], score)
        self.assertEqual(self.client.get(url).json()["score"], score)

    def test_an_active_game_carries_a_null_score(self):
        game = live_game(self.ana)
        self.assertIsNone(self.client.get(reverse("game-state", args=[game.id])).json()["score"])
        self.assertIsNone(post_move(self.client, game, 4).json()["score"])

    def test_a_loss_and_its_replacement_carry_a_null_score(self):
        game = live_game(self.ana, moves_used=299)
        data = post_move(self.client, game, 5).json()
        self.assertEqual(data["ended"]["status"], "lost")
        self.assertIsNone(data["score"])
        self.assertNotIn("score", data["ended"])

    def test_the_stale_409_payload_carries_a_null_score(self):
        game = live_game(self.ana, moves_used=5)
        data = post_move(self.client, game, 4, seq=3).json()
        self.assertEqual(data["error"], "stale")
        self.assertIsNone(data["score"])

    def test_the_stale_409_of_a_finished_win_carries_its_score(self):
        won = make_won(self.ana)
        data = post_move(self.client, won, 4, seq=won.moves_used + 1).json()
        self.assertEqual(data["game"]["status"], "won")
        self.assertEqual(data["score"]["points"], data["score"]["seconds_remaining"] + data["score"]["moves_remaining"])

    def test_the_ended_object_never_has_a_score_key(self):
        game = live_game(self.ana, started_at=timezone.now() - timedelta(hours=1))
        data = self.client.get(reverse("game-state", args=[game.id])).json()
        self.assertEqual(data["ended"]["loss_reason"], "timeout")
        self.assertNotIn("score", data["ended"])


class StaleMoveTests(ApiTestCase):
    def test_stale_seq_is_409_with_the_error_and_the_current_state(self):
        game = live_game(self.ana, moves_used=5)
        response = post_move(self.client, game, 4, seq=3)
        self.assertEqual(response.status_code, 409)
        data = response.json()
        self.assertEqual(data["error"], "stale")
        self.assertEqual(data["game"]["moves_used"], 5)
        self.assertEqual(data["game"]["board"], SCRAMBLED)
        self.assertEqual(data["game"]["status"], "active")
        game.refresh_from_db()
        self.assertEqual(game.moves_used, 5)
        self.assertFalse(Move.objects.exists())

    def test_move_on_a_finished_game_is_409_and_not_counted(self):
        won = make_won(self.ana)
        response = post_move(self.client, won, 4, seq=won.moves_used + 1)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "stale")
        self.assertEqual(response.json()["game"]["status"], "won")
        self.assertFalse(Move.objects.exists())

    def test_move_after_the_deadline_returns_the_timeout_and_the_replacement(self):
        game = live_game(self.ana, board=almost_solved(3), started_at=timezone.now() - timedelta(seconds=181))
        response = post_move(self.client, game, winning_tile(3))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["ended"]["loss_reason"], "timeout")
        self.assertNotEqual(data["game"]["id"], game.id)
        self.assertIsNone(data["move"])
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason), (Game.LOST, Game.TIMEOUT))


class WinOutcomeTests(ApiTestCase):
    def win(self, user, slug):
        game = live_game(user, slug, board=almost_solved({"facil": 3, "normal": 4, "dificil": 5}[slug]))
        size = game.level.size
        self.client.force_login(user)
        response = post_move(self.client, game, winning_tile(size))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["game"]["status"], "won")
        self.assertEqual(data["game"]["id"], game.id)
        self.assertTrue(data["move"]["valid"])
        self.assertIsNone(data["ended"])
        return data

    def test_easy_win_by_a_new_user_offers_normal(self):
        data = self.win(self.ana, "facil")
        self.assertEqual((data["next_level"], data["completed_all"], data["can_switch"]), ("normal", False, False))

    def test_normal_win_offers_hard(self):
        user = make_user("beto")
        make_won(user, "facil")
        data = self.win(user, "normal")
        self.assertEqual((data["next_level"], data["completed_all"], data["can_switch"]), ("dificil", False, False))

    def test_hard_win_completes_the_game(self):
        user = make_user("beto")
        make_won(user, "facil")
        make_won(user, "normal")
        data = self.win(user, "dificil")
        self.assertEqual((data["next_level"], data["completed_all"], data["can_switch"]), (None, True, True))

    def test_win_by_a_completed_user_sets_neither_and_allows_switching(self):
        data = self.win(make_completed_user("beto"), "facil")
        self.assertEqual((data["next_level"], data["completed_all"], data["can_switch"]), (None, False, True))

    def test_a_win_ends_the_game_without_a_replacement(self):
        self.win(self.ana, "facil")
        self.assertFalse(Game.objects.filter(user=self.ana, status=Game.ACTIVE).exists())


class LossOutcomeTests(ApiTestCase):
    def test_move_limit_loss_carries_the_ended_game_and_the_replacement(self):
        game = live_game(self.ana, moves_used=299)
        response = post_move(self.client, game, 5)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["move"], {"tile": 5, "valid": False, "tile_in_place": False})
        self.assertEqual(data["ended"]["id"], game.id)
        self.assertEqual(data["ended"]["status"], "lost")
        self.assertEqual(data["ended"]["loss_reason"], "move_limit")
        self.assertNotEqual(data["game"]["id"], game.id)
        self.assertEqual(data["game"]["status"], "active")
        self.assertEqual(data["game"]["moves_used"], 0)
        self.assertEqual(data["game"]["level"], "facil")
        self.assertEqual(data["game"]["seconds_remaining"], 180)
        self.assertEqual(Game.objects.filter(user=self.ana, status=Game.ACTIVE).count(), 1)

    def test_state_read_of_an_expired_game_returns_the_timeout_and_the_replacement(self):
        game = live_game(self.ana, started_at=timezone.now() - timedelta(hours=1))
        data = self.client.get(reverse("game-state", args=[game.id])).json()
        self.assertEqual(data["ended"]["loss_reason"], "timeout")
        self.assertEqual(data["game"]["moves_used"], 0)
        self.assertEqual(data["game"]["seconds_remaining"], 180)
        self.assertEqual(Game.objects.filter(user=self.ana, status=Game.LOST).count(), 1)
