"""The Perfil page: access, content, expiry, empty state and markup."""

from datetime import timedelta
from html.parser import HTMLParser

from django.db import connection
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from game.models import Game

from .helpers import make_game, make_lost, make_user, make_win

HINT = "suma de los mejores puntajes de cada nivel"
HINT_HTML = f'<span class="visually-hidden">{HINT}</span>'
HEADERS = ["Nivel", "Jugadas", "Ganadas", "Perdidas", "% de victorias", "Mejor puntaje", "Tiempo medio"]
NAMES = ["Fácil", "Normal", "Difícil", "Totales"]


class StatsTable(HTMLParser):
    """Reads the stats table: rows by name, cells by their ``data-label``."""

    def __init__(self, html):
        super().__init__()
        self.rows = {}
        self.caption = ""
        self.col_headers = []
        self.row_headers = []
        self.cells_without_label = 0
        self._in_table = False
        self._tag = None
        self._row = None
        self._cell_label = None
        self._hidden_depth = 0
        self._scope = None
        self._text = ""
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and "stats-table" in (attrs.get("class") or "").split():
            self._in_table = True
        if not self._in_table:
            return
        if tag in ("caption", "th", "td"):
            self._tag = tag
        if tag == "th":
            self._scope = attrs.get("scope")
            self._text = ""
        elif tag == "td":
            self._cell_label = attrs.get("data-label")
            self.cells_without_label += self._cell_label is None
            self._text = ""
        elif tag == "caption":
            self._text = ""
        elif tag == "span" and "visually-hidden" in (attrs.get("class") or "").split():
            self._hidden_depth += 1

    def handle_data(self, data):
        if self._in_table and self._tag and not self._hidden_depth:
            self._text += data

    def handle_endtag(self, tag):
        if not self._in_table:
            return
        if tag == "table":
            self._in_table = False
        elif tag == "span" and self._hidden_depth:
            self._hidden_depth -= 1
        elif tag == "caption" and self._tag == "caption":
            self.caption = self._text.strip()
            self._tag = None
        elif tag == "th" and self._tag == "th":
            text = self._text.strip()
            if self._scope == "col":
                self.col_headers.append(text)
            elif self._scope == "row":
                self.row_headers.append(text)
                self._row = self.rows.setdefault(text, {})
            self._tag = None
        elif tag == "td" and self._tag == "td":
            if self._row is not None and self._cell_label is not None:
                self._row[self._cell_label] = self._text.strip()
            self._tag = None


def stats_table(response):
    return StatsTable(response.content.decode())


class ProfileAccessTests(TestCase):
    def test_the_profile_url_is_perfil(self):
        self.assertEqual(reverse("profile"), "/perfil/")

    def test_anonymous_visitor_is_redirected_to_login_preserving_next(self):
        response = self.client.get("/perfil/")
        self.assertRedirects(response, f"{reverse('login')}?next=/perfil/", fetch_redirect_response=False)

    def test_post_is_405_and_changes_nothing(self):
        ana = make_user("ana")
        make_win(ana, seconds_remaining=80, moves_remaining=260)
        before = list(Game.objects.values())
        self.client.force_login(ana)
        response = self.client.post("/perfil/")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(list(Game.objects.values()), before)

    def test_a_logged_in_user_gets_the_page(self):
        self.client.force_login(make_user("ana"))
        self.assertEqual(self.client.get("/perfil/").status_code, 200)

    def test_the_page_shows_only_the_viewers_own_data(self):
        ana = make_user("ana")
        beto = make_user("beto")
        make_win(ana, seconds_remaining=80, moves_remaining=260)  # ana: 340
        make_win(beto, seconds_remaining=500, moves_remaining=477, seconds=77)  # beto: 977
        make_lost(beto, "normal", seconds=58)
        self.client.force_login(ana)
        response = self.client.get("/perfil/")
        content = response.content.decode()
        self.assertNotIn("beto", content)
        self.assertNotIn("beto@example.com", content)
        self.assertNotIn("977", content)
        table = stats_table(response)
        self.assertEqual(table.rows["Fácil"]["Mejor puntaje"], "340")
        self.assertEqual(table.rows["Normal"]["Jugadas"], "0")
        self.assertEqual(table.rows["Totales"]["Jugadas"], "1")


class ProfileContentTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")
        self.client.force_login(self.ana)

    def table(self):
        return stats_table(self.client.get("/perfil/"))

    def test_rows_appear_in_level_order_then_totals(self):
        self.assertEqual(list(self.table().rows), NAMES)

    def test_the_fácil_row_shows_the_hand_computed_values(self):
        make_win(self.ana, seconds_remaining=80, moves_remaining=260)  # 340
        make_win(self.ana, seconds_remaining=100, moves_remaining=210)  # 310
        make_lost(self.ana, "facil", "timeout", seconds=180)
        make_lost(self.ana, "facil", "abandoned", seconds=30)
        row = self.table().rows["Fácil"]
        self.assertEqual(row["Jugadas"], "4")
        self.assertEqual(row["Ganadas"], "2")
        self.assertEqual(row["Perdidas"], "2")
        self.assertEqual(row["% de victorias"], "50 %")
        self.assertEqual(row["Mejor puntaje"], "340")
        self.assertEqual(self.table().rows["Normal"]["Jugadas"], "0")

    def test_the_totals_row_adds_the_levels_up(self):
        for _ in range(2):
            make_win(self.ana, seconds_remaining=80, moves_remaining=260)
        for _ in range(2):
            make_lost(self.ana, "facil", "move_limit")
        make_win(self.ana, "normal", seconds_remaining=100, moves_remaining=100)
        for _ in range(2):
            make_lost(self.ana, "normal", "timeout", seconds=420)
        totals = self.table().rows["Totales"]
        self.assertEqual(
            [totals[label] for label in ["Jugadas", "Ganadas", "Perdidas", "% de victorias"]],
            ["7", "3", "4", "43 %"],
        )

    def test_no_games_shows_dashes_for_percentage_best_and_average(self):
        for name in NAMES:
            with self.subTest(name):
                row = self.table().rows[name]
                self.assertEqual([row["Jugadas"], row["Ganadas"], row["Perdidas"]], ["0", "0", "0"])
                self.assertEqual(
                    [row["% de victorias"], row["Mejor puntaje"], row["Tiempo medio"]], ["—", "—", "—"]
                )

    def test_average_time_is_mm_ss_over_won_and_lost_attempts(self):
        make_win(self.ana, seconds_remaining=80, moves_remaining=260, seconds=100)
        make_lost(self.ana, "facil", "timeout", seconds=180)
        make_lost(self.ana, "facil", "abandoned", seconds=20)
        self.assertEqual(self.table().rows["Fácil"]["Tiempo medio"], "01:40")

    def test_average_time_rounds_a_half_second_up(self):
        make_win(self.ana, seconds_remaining=80, moves_remaining=260, seconds=100)
        make_win(self.ana, seconds_remaining=80, moves_remaining=260, seconds=101)
        self.assertEqual(self.table().rows["Fácil"]["Tiempo medio"], "01:41")

    def test_win_percent_rounds_halves_up(self):
        make_win(self.ana, seconds_remaining=80, moves_remaining=260)
        for _ in range(7):
            make_lost(self.ana, "facil", "move_limit")
        self.assertEqual(self.table().rows["Fácil"]["% de victorias"], "13 %")

    def test_the_totals_best_is_the_sum_of_the_level_bests_with_a_dash_for_a_level_without_a_win(self):
        make_win(self.ana, seconds_remaining=120, moves_remaining=85)  # 205
        make_win(self.ana, "normal", seconds_remaining=300, moves_remaining=200)  # 1000
        make_lost(self.ana, "dificil", "timeout", seconds=720)
        response = self.client.get("/perfil/")
        table = stats_table(response)
        self.assertEqual(
            [table.rows[name]["Mejor puntaje"] for name in NAMES],
            ["205", "1000", "—", "1205"],
        )
        self.assertContains(response, HINT_HTML, count=1, html=True)

    def test_the_totals_best_over_three_levels_is_a_plain_number_with_the_hidden_hint(self):
        make_win(self.ana, seconds_remaining=120, moves_remaining=85)  # 205
        make_win(self.ana, "normal", seconds_remaining=300, moves_remaining=200)  # 1000
        make_win(self.ana, "dificil", seconds_remaining=500, moves_remaining=300)  # 2400
        response = self.client.get("/perfil/")
        table = stats_table(response)
        self.assertEqual(
            [table.rows[name]["Mejor puntaje"] for name in NAMES],
            ["205", "1000", "2400", "3605"],
        )
        self.assertContains(response, HINT_HTML, count=1, html=True)
        # The hint belongs to the totals cell, after its number.
        self.assertRegex(response.content.decode(), r"3605\s*" + HINT_HTML)

    def test_the_hint_is_omitted_when_the_totals_best_is_a_dash(self):
        make_lost(self.ana, "facil", "timeout", seconds=180)
        response = self.client.get("/perfil/")
        self.assertNotContains(response, HINT)
        self.assertNotContains(response, "visually-hidden")
        table = stats_table(response)
        self.assertEqual([table.rows[name]["Mejor puntaje"] for name in NAMES], ["—"] * 4)

    def test_the_level_name_cell_is_not_a_data_cell(self):
        self.assertEqual(self.table().row_headers, NAMES)


class ProfileExpiryAndEmptyTests(TestCase):
    EMPTY = "Aún no has terminado ninguna partida"

    def setUp(self):
        self.ana = make_user("ana")
        self.client.force_login(self.ana)

    def test_an_expired_unfinalized_attempt_is_recorded_as_a_timeout_loss(self):
        started = timezone.now() - timedelta(hours=1, seconds=180)
        game = make_game(self.ana, "facil", started_at=started)
        response = self.client.get("/perfil/")
        game.refresh_from_db()
        self.assertEqual((game.status, game.loss_reason), (Game.LOST, Game.TIMEOUT))
        self.assertEqual(game.ended_at, game.deadline)
        self.assertFalse(Game.objects.filter(user=self.ana, status=Game.ACTIVE).exists())
        self.assertEqual(Game.objects.filter(user=self.ana).count(), 1)
        row = stats_table(response).rows["Fácil"]
        self.assertEqual([row["Jugadas"], row["Ganadas"], row["Perdidas"]], ["1", "0", "1"])

    def test_a_running_attempt_stays_active_unchanged_and_uncounted(self):
        game = make_game(self.ana, "facil", started_at=timezone.now() - timedelta(seconds=10))
        before = Game.objects.filter(pk=game.pk).values().get()
        response = self.client.get("/perfil/")
        self.assertEqual(Game.objects.filter(pk=game.pk).values().get(), before)
        self.assertEqual(Game.objects.get(pk=game.pk).status, Game.ACTIVE)
        self.assertEqual(stats_table(response).rows["Totales"]["Jugadas"], "0")

    def test_another_users_expired_attempt_is_left_unchanged(self):
        beto = make_user("beto")
        game = make_game(beto, "facil", started_at=timezone.now() - timedelta(hours=2))
        before = Game.objects.filter(pk=game.pk).values().get()
        self.client.get("/perfil/")
        self.assertEqual(Game.objects.filter(pk=game.pk).values().get(), before)
        self.assertEqual(Game.objects.get(pk=game.pk).status, Game.ACTIVE)

    def test_a_new_user_gets_the_empty_state_with_a_link_to_play_and_a_table_of_zeros(self):
        response = self.client.get("/perfil/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.EMPTY)
        self.assertContains(response, f'href="{reverse("play")}"')
        table = stats_table(response)
        self.assertEqual(list(table.rows), NAMES)
        for name in NAMES:
            self.assertEqual(table.rows[name]["Jugadas"], "0")
            self.assertEqual(table.rows[name]["Mejor puntaje"], "—")

    def test_a_user_whose_only_attempt_is_active_sees_the_empty_state(self):
        make_game(self.ana, "facil", started_at=timezone.now() - timedelta(seconds=10))
        self.assertContains(self.client.get("/perfil/"), self.EMPTY)

    def test_the_empty_state_disappears_once_an_attempt_has_finished(self):
        make_lost(self.ana, "facil", "timeout", seconds=180)
        self.assertNotContains(self.client.get("/perfil/"), self.EMPTY)


class ProfileMarkupTests(TestCase):
    def setUp(self):
        self.ana = make_user("ana")
        self.client.force_login(self.ana)
        make_win(self.ana, seconds_remaining=120, moves_remaining=85)
        make_lost(self.ana, "facil", "timeout", seconds=180)

    def test_every_data_cell_carries_a_data_label(self):
        table = stats_table(self.client.get("/perfil/"))
        self.assertEqual(table.cells_without_label, 0)
        for name in NAMES:
            self.assertEqual(list(table.rows[name]), HEADERS[1:])

    def test_the_table_has_a_caption_and_scoped_headers(self):
        table = stats_table(self.client.get("/perfil/"))
        self.assertTrue(table.caption)
        self.assertEqual(table.col_headers, HEADERS)
        self.assertEqual(table.row_headers, NAMES)

    def test_the_page_extends_the_base_layout(self):
        response = self.client.get("/perfil/")
        self.assertTemplateUsed(response, "scoring/profile.html")
        self.assertTemplateUsed(response, "base.html")

    def test_the_query_count_does_not_depend_on_the_number_of_attempts(self):
        def count_queries(user):
            self.client.force_login(user)
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get("/perfil/").status_code, 200)
            return len(queries)

        newcomer = make_user("newcomer")
        veteran = make_user("veteran")
        for slug in ["facil", "normal", "dificil"]:
            for _ in range(4):
                make_win(veteran, slug, seconds_remaining=50, moves_remaining=50)
                make_lost(veteran, slug, "abandoned")
        self.assertEqual(count_queries(newcomer), count_queries(veteran))


class ProfileCssTests(SimpleTestCase):
    def setUp(self):
        self.css = (settings.BASE_DIR / "static" / "css" / "site.css").read_text()

    def test_the_stylesheet_defines_the_stacked_stats_table(self):
        self.assertIn(".stats-table", self.css)
        self.assertIn("attr(data-label)", self.css)
        self.assertIn(".visually-hidden", self.css)

    def test_the_clipped_text_cannot_widen_the_page(self):
        # Found at 360 px: the nowrap hint and header row added 21 px of scroll width.
        # `contain: paint` clips them without the forbidden overflow property.
        hidden_rule = self.css.partition(".visually-hidden,")[2].partition("}")[0]
        self.assertIn("contain: paint", hidden_rule)

    def test_the_stats_table_stacks_below_40rem_and_returns_from_40rem(self):
        base, _, wide = self.css.partition("@media (min-width: 40rem)")
        self.assertIn("grid-template-columns: minmax(0, 1fr) auto", base)
        self.assertIn("display: table-row", wide)

    def test_the_stylesheet_still_never_hides_horizontal_overflow(self):
        self.assertNotIn("overflow-x", self.css)
        self.assertNotRegex(self.css, r"overflow:\s*hidden")
