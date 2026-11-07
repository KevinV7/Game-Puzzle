"""Privacy baseline: only the leaderboard shows another user, and only username and score."""

import re
from html.parser import HTMLParser
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from game.models import Game

from .helpers import at, make_game, make_lost, make_user, make_win

TABLE_ATTRIBUTES = {"class", "aria-current", "scope", "colspan"}


class Attributes(HTMLParser):
    """Collects every ``(tag, attribute, value)`` inside ``<main>`` and every link target."""

    def __init__(self, html):
        super().__init__()
        self.in_main = False
        self.main = []
        self.hrefs = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.in_main = True
        for name, value in attrs:
            if tag == "a" and name == "href":
                self.hrefs.append(value)
            if self.in_main:
                self.main.append((tag, name, value))

    def handle_endtag(self, tag):
        if tag == "main":
            self.in_main = False


class LeaderboardPrivacyTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")
        self.beto = make_user("beto")
        self.beto_win = make_win(self.beto, seconds_remaining=500, moves_remaining=477, seconds=77, ended_at=at(7700))
        make_lost(self.beto, "normal", seconds=58)
        make_game(self.beto, "dificil")  # an active attempt of beto
        self.client.force_login(self.ana)
        self.response = self.client.get("/clasificacion/")
        self.content = self.response.content.decode()

    def test_beto_and_his_score_are_shown(self):
        self.assertContains(self.response, "beto")
        self.assertContains(self.response, "977")

    def test_no_email_of_another_user_is_shown(self):
        self.assertNotContains(self.response, "beto@example.com")
        self.assertNotContains(self.response, "@example.com")

    def test_no_attempt_timing_or_date_of_beto_is_shown(self):
        game = self.beto_win
        for private in [
            str(game.started_at.year),
            game.ended_at.isoformat(),
            "01:17",  # his 77 s duration
            "00:58",  # the timeout/abandon lengths
            "<time",
            "datetime",
        ]:
            with self.subTest(private):
                self.assertNotIn(private, self.content)

    def test_no_board_or_loss_data_is_shown(self):
        for private in ["initial_board", "moves", "tablero", "perdidas", "derrota", "timeout", "abandoned"]:
            with self.subTest(private):
                self.assertNotIn(private, self.content.lower())

    def test_the_table_markup_carries_no_identifiers_or_links(self):
        attributes = Attributes(self.content)
        self.assertEqual({name for _, name, _ in attributes.main} - TABLE_ATTRIBUTES, set())
        for href in attributes.hrefs:
            self.assertNotRegex(href, rf"/{self.beto.pk}(/|$)")
            self.assertNotRegex(href, rf"/{self.beto_win.pk}(/|$)")
        self.assertEqual(
            sorted(set(attributes.hrefs)),
            sorted({reverse(name) for name in ["home", "instructions", "play", "leaderboard", "profile"]}),
        )

    def test_a_username_with_markup_is_escaped(self):
        # The signup validators forbid "<", so the row is built straight in the database.
        get_user_model().objects.filter(pk=self.beto.pk).update(username="<b>x</b>")
        content = self.client.get("/clasificacion/").content.decode()
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", content)
        self.assertNotIn("<b>x</b>", content)

    def test_the_templates_never_mark_anything_safe(self):
        for path in (settings.BASE_DIR / "templates").rglob("*.html"):
            with self.subTest(path.name):
                self.assertNotRegex(path.read_text(), r"\|\s*safe|mark_safe|autoescape\s+off")

    def test_a_user_cannot_read_an_attempt_of_beto_through_the_state_endpoint(self):
        game = Game.objects.filter(user=self.beto).first()
        response = self.client.get(reverse("game-state", args=[game.pk]))
        self.assertEqual(response.status_code, 404)


class OtherPagesNeverShowOtherUsersTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")
        beto = make_user("beto")
        make_win(beto, seconds_remaining=500, moves_remaining=477, ended_at=at(100))
        make_win(beto, "normal", seconds_remaining=400, moves_remaining=100, ended_at=at(200))
        make_lost(beto, "dificil", "timeout")
        make_game(self.ana, started_at=timezone.now())  # without an active attempt the board URL only redirects
        self.client.force_login(self.ana)

    def test_no_page_but_the_leaderboard_shows_beto(self):
        urls = [reverse(name) for name in ["home", "instructions", "play", "profile"]]
        urls.append(reverse("game-board", args=["facil"]))
        # The CSRF token is random text in every form. It is forced to contain both
        # strings below so the test cannot fail by chance, and removed before looking.
        with mock.patch("django.template.context_processors.get_token", return_value="x977betoy"):
            for url in urls:
                with self.subTest(url):
                    content = self.client.get(url).content.decode()
                    self.assertIn("x977betoy", content)
                    content = content.replace("x977betoy", "")
                    self.assertNotIn("beto", content)
                    self.assertNotIn("977", content)

    def test_the_leaderboard_is_the_only_page_that_does(self):
        self.assertContains(self.client.get(reverse("leaderboard")), "beto")


class TemplateSourceTests(SimpleTestCase):
    def test_the_leaderboard_template_has_no_user_id_variables(self):
        source = (settings.BASE_DIR / "templates" / "scoring" / "leaderboard.html").read_text()
        self.assertNotRegex(source, re.compile(r"user_id|\.pk|\.id\b|email"))
