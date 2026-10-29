"""HTML pages: level selection, start and switch confirmation, and the static board shell."""

import json
import re
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from game.models import Game
from game.tests.helpers import make_completed_user, make_game, make_user, make_user_who_won

FORBIDDEN_SCORE_WORDS = ["puntuación", "puntos", "score"]


def start_form(slug):
    return f'action="{reverse("game-start", args=[slug])}"'


def live_game(user, slug="facil", **fields):
    fields.setdefault("started_at", timezone.now())
    return make_game(user, slug, **fields)


def card(response, name):
    """The HTML of the level card titled ``name``."""
    content = response.content.decode()
    match = re.search(rf'<article class="level-card[^"]*">(?:(?!</article>).)*?<h2>{name}</h2>.*?</article>', content, re.S)
    assert match, f"no card for {name}"
    return match.group(0)


class ViewTestCase(TestCase):
    def setUp(self):
        self.ana = make_user("ana")
        self.client.force_login(self.ana)


class LevelSelectionTests(ViewTestCase):
    def test_new_user_sees_three_summaries_and_only_easy_can_start(self):
        response = self.client.get(reverse("play"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Aún no disponible")
        for text in ["3x3", "4x4", "5x5", "3 min", "7 min", "12 min", "300 movimientos", "700 movimientos", "1500 movimientos", "x1", "x2", "x3"]:
            with self.subTest(text):
                self.assertContains(response, text)
        self.assertContains(response, start_form("facil"))
        for slug, name, hint in [("normal", "Normal", "Gana Fácil"), ("dificil", "Difícil", "Gana Normal")]:
            with self.subTest(slug):
                self.assertNotContains(response, start_form(slug))
                self.assertIn("Bloqueado", card(response, name))
                self.assertIn(f"{hint} para desbloquear", card(response, name))
        self.assertNotContains(response, "Continuar")

    def test_after_easy_only_normal_can_start(self):
        user = make_user_who_won(["facil"], "beto")
        self.client.force_login(user)
        response = self.client.get(reverse("play"))
        self.assertNotContains(response, start_form("facil"))
        self.assertIn("Superado", card(response, "Fácil"))
        self.assertContains(response, start_form("normal"))
        self.assertNotContains(response, start_form("dificil"))
        self.assertIn("Bloqueado", card(response, "Difícil"))

    def test_completed_user_can_start_every_level_and_passed_stay_marked(self):
        self.client.force_login(make_completed_user("beto"))
        response = self.client.get(reverse("play"))
        for slug in ["facil", "normal", "dificil"]:
            self.assertContains(response, start_form(slug))
        for name in ["Fácil", "Normal", "Difícil"]:
            self.assertIn("Superado", card(response, name))
            self.assertNotIn("Bloqueado", card(response, name))

    def test_start_forms_post_with_a_csrf_token(self):
        response = self.client.get(reverse("play"))
        self.assertContains(response, 'method="post"')
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_active_attempt_adds_a_continue_link_to_its_board(self):
        live_game(self.ana, "facil")
        response = self.client.get(reverse("play"))
        self.assertContains(response, f'<a class="button" href="{reverse("game-board", args=["facil"])}">Continuar</a>', html=True)
        self.assertContains(response, "Fácil")

    def test_expired_attempt_is_recorded_once_and_no_clock_starts(self):
        game = live_game(self.ana, started_at=timezone.now() - timedelta(hours=1))
        for _ in range(2):
            response = self.client.get(reverse("play"))
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Continuar")
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason), (Game.LOST, Game.TIMEOUT))
        self.assertEqual(Game.objects.filter(user=self.ana).count(), 1)
        self.assertFalse(Game.objects.filter(status=Game.ACTIVE).exists())

    def test_the_page_shows_no_other_users_data(self):
        live_game(make_user("beto"), "facil")
        self.assertNotContains(self.client.get(reverse("play")), "beto")


class StartTests(ViewTestCase):
    def test_start_creates_the_game_and_redirects_with_303_to_the_board(self):
        response = self.client.post(reverse("game-start", args=["facil"]))
        self.assertRedirects(response, reverse("game-board", args=["facil"]), status_code=303, fetch_redirect_response=False)
        game = Game.objects.get(user=self.ana)
        self.assertEqual((game.level.slug, game.status, game.moves_used), ("facil", Game.ACTIVE, 0))

    def test_starting_the_same_level_again_keeps_the_attempt(self):
        game = live_game(self.ana, moves_used=10)
        response = self.client.post(reverse("game-start", args=["facil"]))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(Game.objects.get(user=self.ana).pk, game.pk)

    def test_start_by_get_is_405(self):
        self.assertEqual(self.client.get(reverse("game-start", args=["facil"])).status_code, 405)

    def test_start_without_csrf_token_is_403_and_creates_nothing(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.ana)
        self.assertEqual(client.post(reverse("game-start", args=["facil"])).status_code, 403)
        self.assertFalse(Game.objects.exists())

    def test_locked_level_is_a_spanish_403_page_that_links_back_and_creates_nothing(self):
        response = self.client.post(reverse("game-start", args=["normal"]))
        self.assertContains(response, "no está disponible", status_code=403)
        self.assertContains(response, f'href="{reverse("play")}"', status_code=403)
        self.assertNotContains(response, "<canvas", status_code=403)
        self.assertFalse(Game.objects.exists())

    def test_locked_start_leaves_the_active_attempt_untouched(self):
        game = live_game(self.ana)
        self.assertEqual(self.client.post(reverse("game-start", args=["dificil"])).status_code, 403)
        game.refresh_from_db()
        self.assertEqual(game.status, Game.ACTIVE)

    def test_unknown_slug_is_a_spanish_404_page(self):
        response = self.client.post(reverse("game-start", args=["inexistente"]))
        self.assertContains(response, "Nivel no encontrado", status_code=404)
        self.assertContains(response, f'href="{reverse("play")}"', status_code=404)
        self.assertFalse(Game.objects.exists())

    def test_anonymous_start_is_redirected_to_login(self):
        url = reverse("game-start", args=["facil"])
        response = Client().post(url)
        self.assertRedirects(response, f"{reverse('login')}?next={url}", fetch_redirect_response=False)


class SwitchConfirmationTests(TestCase):
    def setUp(self):
        self.user = make_completed_user("ana")
        self.client.force_login(self.user)
        self.easy = live_game(self.user, "facil", moves_used=7)
        self.url = reverse("game-start", args=["normal"])

    def test_switch_without_confirmation_is_a_409_warning_page_and_changes_nothing(self):
        response = self.client.post(self.url)
        self.assertContains(response, "contará como perdida", status_code=409)
        self.assertContains(response, 'name="confirmar" value="1"', status_code=409)
        self.assertContains(response, f'action="{self.url}"', status_code=409)
        self.assertContains(response, f'href="{reverse("game-board", args=["facil"])}"', status_code=409)
        self.assertContains(response, "Cancelar", status_code=409)
        self.easy.refresh_from_db()
        self.assertEqual((self.easy.status, self.easy.moves_used), (Game.ACTIVE, 7))
        self.assertEqual(Game.objects.filter(user=self.user, status=Game.ACTIVE).count(), 1)

    def test_any_value_but_one_is_not_a_confirmation(self):
        for value in ["0", "", "true", "si"]:
            with self.subTest(value):
                self.assertEqual(self.client.post(self.url, {"confirmar": value}).status_code, 409)
        self.easy.refresh_from_db()
        self.assertEqual(self.easy.status, Game.ACTIVE)

    def test_confirmed_switch_abandons_the_old_attempt_and_starts_the_new_one(self):
        response = self.client.post(self.url, {"confirmar": "1"})
        self.assertRedirects(response, reverse("game-board", args=["normal"]), status_code=303, fetch_redirect_response=False)
        self.easy.refresh_from_db()
        self.assertEqual((self.easy.status, self.easy.loss_reason), (Game.LOST, Game.ABANDONED))
        self.assertEqual(Game.objects.get(user=self.user, status=Game.ACTIVE).level.slug, "normal")


class BoardShellTests(ViewTestCase):
    def state_of(self, response):
        match = re.search(r'<script id="game-state" type="application/json">(.*?)</script>', response.content.decode(), re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_without_an_attempt_it_redirects_to_the_level_selection(self):
        response = self.client.get(reverse("game-board", args=["facil"]))
        self.assertRedirects(response, reverse("play"), fetch_redirect_response=False)

    def test_an_attempt_on_another_level_does_not_open_this_board(self):
        user = make_user_who_won(["facil"], "beto")
        live_game(user, "normal")
        self.client.force_login(user)
        response = self.client.get(reverse("game-board", args=["facil"]))
        self.assertRedirects(response, reverse("play"), fetch_redirect_response=False)

    def test_unknown_slug_is_a_spanish_404_page(self):
        self.assertContains(self.client.get(reverse("game-board", args=["inexistente"])), "Nivel no encontrado", status_code=404)

    def test_anonymous_visitor_is_redirected_to_login(self):
        url = reverse("game-board", args=["facil"])
        self.assertRedirects(Client().get(url), f"{reverse('login')}?next={url}", fetch_redirect_response=False)

    def test_the_shell_embeds_the_state_and_renders_a_static_numbered_grid_per_size(self):
        for slug, size in [("facil", 3), ("normal", 4), ("dificil", 5)]:
            with self.subTest(slug):
                user = make_completed_user(f"user{size}")
                self.client.force_login(user)
                game = live_game(user, slug)
                response = self.client.get(reverse("game-board", args=[slug]))
                self.assertEqual(response.status_code, 200)
                state = self.state_of(response)
                self.assertEqual(state["game"]["id"], game.id)
                self.assertEqual(state["game"]["board"], game.board)
                content = response.content.decode()
                self.assertEqual(len(re.findall(r'<li class="tile(?: tile-gap)?"', content)), size * size)
                self.assertEqual(content.count('class="tile tile-gap"'), 1)
                self.assertIn(f"--size: {size}", content)
                for number in range(1, size * size):
                    self.assertRegex(content, rf'<li class="tile">{number}</li>')

    def test_the_shell_shows_counters_and_a_live_region_notice(self):
        live_game(self.ana, moves_used=15, started_at=timezone.now() - timedelta(seconds=60))
        response = self.client.get(reverse("game-board", args=["facil"]))
        self.assertContains(response, "Movimientos")
        self.assertContains(response, "15 / 300")
        self.assertContains(response, "Tiempo")
        self.assertRegex(response.content.decode(), r"0[12]:[0-9]{2}")
        self.assertContains(response, 'aria-live="polite"')

    def test_before_completion_there_is_no_restart_and_no_level_switch(self):
        live_game(self.ana)
        content = self.client.get(reverse("game-board", args=["facil"])).content.decode()
        for forbidden in ["Elegir otro nivel", "Reiniciar", "Reintentar", "Empezar de nuevo", *FORBIDDEN_SCORE_WORDS]:
            with self.subTest(forbidden):
                self.assertNotIn(forbidden.lower(), content.lower())
        self.assertNotIn(start_form("normal"), content)

    def test_after_completion_the_shell_links_to_the_level_selection(self):
        user = make_completed_user("beto")
        self.client.force_login(user)
        live_game(user)
        response = self.client.get(reverse("game-board", args=["facil"]))
        self.assertContains(response, f'<a href="{reverse("play")}">Elegir otro nivel</a>', html=True)
        self.assertTrue(self.state_of(response)["can_switch"])

    def test_the_win_panel_is_hidden_and_free_of_scores(self):
        live_game(self.ana)
        content = self.client.get(reverse("game-board", args=["facil"])).content.decode()
        self.assertRegex(content, r'<section[^>]*id="win-panel"[^>]*hidden')
        for word in FORBIDDEN_SCORE_WORDS:
            self.assertNotIn(word, content.lower())

    def test_viewing_the_board_of_an_expired_attempt_shows_its_replacement(self):
        old = live_game(self.ana, started_at=timezone.now() - timedelta(hours=1))
        response = self.client.get(reverse("game-board", args=["facil"]))
        self.assertEqual(response.status_code, 200)
        state = self.state_of(response)
        self.assertNotEqual(state["game"]["id"], old.id)
        self.assertEqual(state["game"]["moves_used"], 0)
        self.assertEqual(Game.objects.filter(user=self.ana, loss_reason=Game.TIMEOUT).count(), 1)
        self.assertEqual(Game.objects.filter(user=self.ana, status=Game.ACTIVE).count(), 1)
