"""The Clasificación page: access, tables, ordering, empty states, own position and highlight."""

import re
from html.parser import HTMLParser

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .helpers import at, make_user, make_win

EMPTY = "Aún no hay puntajes en este nivel"
HEADERS = ["Posición", "Jugador", "Puntaje"]
LEVELS = ["Fácil", "Normal", "Difícil"]


class Board:
    """One level section of the page."""

    def __init__(self, title):
        self.title = title
        self.caption = ""
        self.col_headers = []
        self.rows = []
        self.empty_texts = []
        self.own_texts = []

    def usernames(self):
        return [row["cells"][1].removesuffix("(tú)").strip() for row in self.rows]


class LeaderboardPage(HTMLParser):
    """Reads the page: one ``Board`` per ``<section>``, rows as lists of cell texts."""

    def __init__(self, html):
        super().__init__()
        self.boards = []
        self.tables = 0
        self._board = None
        self._row = None
        self._text = None
        self._text_target = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "section":
            self._board = Board("")
            self.boards.append(self._board)
        elif self._board is None:
            return
        elif tag == "h2":
            self._start_text("title")
        elif tag == "table":
            self.tables += 1
        elif tag == "caption":
            self._start_text("caption")
        elif tag == "th" and attrs.get("scope") == "col":
            self._start_text("header")
        elif tag == "tr" and self._text_target != "header":
            self._row = {
                "cells": [],
                "class": classes,
                "aria_current": attrs.get("aria-current"),
                "empty": "empty-row" in classes,
            }
        elif tag == "td":
            self._start_text("cell")
        elif tag == "p" and "own-position" in classes:
            self._start_text("own")

    def _start_text(self, target):
        self._text, self._text_target = [], target

    def handle_data(self, data):
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if self._board is None or self._text is None:
            if tag == "tr" and self._row is not None and self._board is not None:
                self._close_row()
            if tag == "section":
                self._board = None
            return
        text = " ".join("".join(self._text).split())
        target = self._text_target
        if (target, tag) in {("title", "h2"), ("caption", "caption"), ("header", "th"), ("cell", "td"), ("own", "p")}:
            self._text = self._text_target = None
            if target == "title":
                self._board.title = text
            elif target == "caption":
                self._board.caption = text
            elif target == "header":
                self._board.col_headers.append(text)
            elif target == "cell" and self._row is not None:
                self._row["cells"].append(text)
            elif target == "own":
                self._board.own_texts.append(text)
        if tag == "tr" and self._row is not None:
            self._close_row()

    def _close_row(self):
        row, self._row = self._row, None
        if row["empty"]:
            self._board.empty_texts.append(row["cells"][0] if row["cells"] else "")
        elif row["cells"]:
            self._board.rows.append(row)


def page(response):
    return LeaderboardPage(response.content.decode())


def win(user, slug="facil", points=100, **fields):
    """A win worth exactly ``points`` on Easy (x1): seconds + moves."""
    moves = min(points, 10)
    return make_win(user, slug, seconds_remaining=points - moves, moves_remaining=moves, **fields)


class LeaderboardPageTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")
        self.client.force_login(self.ana)

    def boards(self):
        return page(self.client.get("/clasificacion/")).boards

    def easy_names(self):
        return self.boards()[0].usernames()

    def test_the_leaderboard_url_is_clasificacion(self):
        self.assertEqual(reverse("leaderboard"), "/clasificacion/")

    def test_anonymous_visitor_is_redirected_to_login_preserving_next(self):
        self.client.logout()
        response = self.client.get("/clasificacion/")
        self.assertRedirects(response, f"{reverse('login')}?next=/clasificacion/", fetch_redirect_response=False)

    def test_post_is_405(self):
        self.assertEqual(self.client.post("/clasificacion/").status_code, 405)

    def test_a_logged_in_user_gets_a_real_page(self):
        response = self.client.get("/clasificacion/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Aún no disponible")
        self.assertTemplateUsed(response, "scoring/leaderboard.html")
        self.assertTemplateUsed(response, "base.html")

    def test_three_tables_titled_facil_normal_dificil_in_that_order(self):
        boards = self.boards()
        self.assertEqual([board.title for board in boards], LEVELS)
        self.assertEqual([board.caption for board in boards], [f"Clasificación {name}" for name in LEVELS])

    def test_every_table_has_scoped_column_headers(self):
        for board in self.boards():
            self.assertEqual(board.col_headers, HEADERS)

    def test_headers_use_th_scope_col(self):
        content = self.client.get("/clasificacion/").content.decode()
        self.assertEqual(len(re.findall(r'<th scope="col">', content)), 9)

    def test_a_user_appears_once_with_the_best_score(self):
        for points in (200, 340, 310):
            win(self.ana, points=points, ended_at=at(points))
        board = self.boards()[0]
        self.assertEqual(board.usernames(), ["ana"])
        self.assertEqual(board.rows[0]["cells"], ["1", "ana (tú)", "340"])

    def test_twelve_users_give_exactly_ten_rows(self):
        for i in range(12):
            win(make_user(f"user{i:02d}"), points=100 + i * 10, ended_at=at(i))
        board = self.boards()[0]
        self.assertEqual(len(board.rows), 10)
        self.assertEqual([row["cells"][0] for row in board.rows], [str(n) for n in range(1, 11)])
        self.assertEqual(board.rows[0]["cells"][1:], ["user11", "210"])
        self.assertEqual(board.rows[-1]["cells"][1:], ["user02", "120"])

    def test_rows_follow_the_score_order(self):
        win(self.ana, points=300, ended_at=at(1))
        win(make_user("beto"), points=340, ended_at=at(2))
        win(make_user("carla"), points=250, ended_at=at(3))
        self.assertEqual(self.easy_names(), ["beto", "ana", "carla"])

    def test_a_tie_on_points_goes_to_the_earlier_end_time(self):
        win(self.ana, points=340, ended_at=at(3600))
        win(make_user("beto"), points=340, ended_at=at(0))
        self.assertEqual(self.easy_names(), ["beto", "ana"])

    def test_the_levels_are_scored_with_their_multiplier(self):
        win(self.ana, "normal", points=100, ended_at=at(1))
        board = self.boards()[1]
        self.assertEqual(board.rows[0]["cells"], ["1", "ana (tú)", "200"])


class LeaderboardStateTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")
        self.client.force_login(self.ana)

    def boards(self):
        return page(self.client.get("/clasificacion/")).boards

    def fill(self, count, points, slug="facil", prefix="rival"):
        for i in range(count):
            win(make_user(f"{prefix}{i:02d}"), slug, points=points, ended_at=at(i))

    def test_a_level_without_wins_shows_the_empty_state_and_the_others_are_unaffected(self):
        win(make_user("beto"), points=300, ended_at=at(1))
        facil, normal, dificil = self.boards()
        self.assertEqual(facil.rows[0]["cells"], ["1", "beto", "300"])
        self.assertEqual(facil.empty_texts, [])
        for board in (normal, dificil):
            self.assertEqual(board.empty_texts, [EMPTY])
            self.assertEqual(board.rows, [])

    def test_no_wins_at_all_gives_three_empty_states(self):
        response = self.client.get("/clasificacion/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([board.empty_texts for board in page(response).boards], [[EMPTY]] * 3)
        self.assertEqual(response.content.decode().count(EMPTY), 3)

    def test_the_viewer_outside_the_top_ten_sees_their_position_under_the_table(self):
        self.fill(11, 300)
        win(self.ana, points=205, ended_at=at(1))
        facil = self.boards()[0]
        self.assertEqual(len(facil.rows), 10)
        self.assertEqual(facil.own_texts, ["Tu posición: 12 (205 puntos)"])
        self.assertFalse(any(row["aria_current"] for row in facil.rows))

    def test_no_position_line_when_the_viewer_is_inside_the_top_ten(self):
        win(self.ana, points=300, ended_at=at(1))
        self.fill(2, 200)
        facil = self.boards()[0]
        self.assertEqual(facil.usernames()[0], "ana")
        self.assertEqual(facil.own_texts, [])

    def test_no_position_line_when_the_viewer_has_no_win_on_the_level(self):
        self.fill(12, 300)
        win(self.ana, "normal", points=100, ended_at=at(1))
        facil = self.boards()[0]
        self.assertEqual(len(facil.rows), 10)
        self.assertEqual(facil.own_texts, [])

    def test_a_tie_reached_later_ranks_after_the_ten_earlier_ones(self):
        self.fill(10, 400)
        win(self.ana, points=400, ended_at=at(3600))
        self.assertEqual(self.boards()[0].own_texts, ["Tu posición: 11 (400 puntos)"])

    def test_one_point_is_singular(self):
        self.fill(10, 300)
        win(self.ana, points=1, ended_at=at(1))
        self.assertEqual(self.boards()[0].own_texts, ["Tu posición: 11 (1 punto)"])

    def test_zero_points_stay_plural(self):
        self.fill(10, 300)
        win(self.ana, points=0, ended_at=at(1))
        self.assertEqual(self.boards()[0].own_texts, ["Tu posición: 11 (0 puntos)"])

    def test_only_the_viewers_own_row_is_highlighted_and_marked(self):
        self.fill(2, 300)
        win(self.ana, points=250, ended_at=at(1))
        rows = self.boards()[0].rows
        self.assertEqual([row["cells"][1] for row in rows], ["rival00", "rival01", "ana (tú)"])
        self.assertEqual([row["class"] for row in rows], [[], [], ["is-viewer"]])
        self.assertEqual([row["aria_current"] for row in rows], [None, None, "true"])

    def test_the_marker_is_visible_text_not_only_an_attribute(self):
        win(self.ana, points=250, ended_at=at(1))
        self.assertContains(self.client.get("/clasificacion/"), "(tú)", count=1)

    def test_a_table_without_a_viewer_row_has_no_highlight(self):
        self.fill(2, 300)
        win(self.ana, "normal", points=250, ended_at=at(1))
        facil, normal, dificil = self.boards()
        self.assertFalse(any(row["class"] or row["aria_current"] for row in facil.rows))
        self.assertEqual(normal.rows[0]["class"], ["is-viewer"])
        self.assertEqual(dificil.rows, [])
        content = self.client.get("/clasificacion/").content.decode()
        self.assertEqual(content.count('class="is-viewer"'), 1)

    def test_the_viewers_levels_are_never_summed(self):
        win(self.ana, "facil", points=205, ended_at=at(1))
        make_win(self.ana, "normal", seconds_remaining=400, moves_remaining=100, ended_at=at(2))  # 500 x 2
        boards = self.boards()
        self.assertEqual(boards[0].rows[0]["cells"][2], "205")
        self.assertEqual(boards[1].rows[0]["cells"][2], "1000")
        self.assertNotContains(self.client.get("/clasificacion/"), "1205")


class LeaderboardCssTests(SimpleTestCase):
    def setUp(self):
        self.css = (settings.BASE_DIR / "static" / "css" / "site.css").read_text()

    def test_the_stylesheet_defines_the_viewer_highlight_and_position_line(self):
        self.assertIn(".is-viewer", self.css)
        self.assertIn(".own-position", self.css)
        self.assertIn(".leaderboard-table", self.css)

    def test_the_placeholder_rule_is_gone(self):
        self.assertNotIn(".placeholder", self.css)

    def test_the_stylesheet_never_hides_horizontal_overflow(self):
        self.assertNotIn("overflow-x", self.css)
        self.assertNotRegex(self.css, r"overflow:\s*hidden")
