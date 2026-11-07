from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse


class ResponsiveStylesheetTests(SimpleTestCase):
    def setUp(self):
        self.css = (settings.BASE_DIR / "static" / "css" / "site.css").read_text()

    def test_stylesheet_applies_the_responsive_safeguards(self):
        for rule in ["box-sizing: border-box", "max-width: 100%", "overflow-wrap: anywhere", "flex-wrap: wrap"]:
            with self.subTest(rule):
                self.assertIn(rule, self.css)

    def test_stylesheet_never_hides_horizontal_overflow(self):
        self.assertNotIn("overflow-x", self.css)
        self.assertNotRegex(self.css, r"overflow:\s*hidden")


class InstructionsPageTests(TestCase):
    def setUp(self):
        self.response = self.client.get(reverse("instructions"))

    def test_instructions_are_public_and_not_the_construction_stub(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertNotContains(self.response, "Página en construcción")

    def test_instructions_have_sections_for_rules_and_each_control(self):
        for heading in ["Reglas", "Teclado", "Ratón", "Pantalla táctil"]:
            with self.subTest(heading):
                self.assertContains(self.response, f"<h2>{heading}</h2>", html=True)

    def test_instructions_describe_the_rules_and_the_controls(self):
        for text in ["espacio vacío", "flechas", "clic", "Toca una ficha", "desliza el dedo"]:
            with self.subTest(text):
                self.assertContains(self.response, text)

    def test_instructions_include_a_3x3_illustration_with_alt_text(self):
        content = self.response.content.decode()
        self.assertRegex(content, r'role="img"\s+aria-label="[^"]+"')
        self.assertEqual(content.count('class="example-tile'), 9)
        self.assertEqual(content.count('class="example-tile example-tile-empty"'), 1)


class HomePageTests(TestCase):
    def test_home_is_public_and_shows_the_game_name(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<h1>Puzle deslizante</h1>", html=True)

    def test_home_is_a_real_page_not_the_construction_stub(self):
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, "Página en construcción")

    def test_home_shows_a_play_button_and_links_to_instructions_and_leaderboard(self):
        response = self.client.get(reverse("home"))
        self.assertContains(
            response,
            f'<a class="button" href="{reverse("play")}">Jugar</a>',
            html=True,
        )
        main = response.content.decode().split('<main class="site-main">')[1]
        self.assertIn(f'href="{reverse("instructions")}"', main)
        self.assertIn(f'href="{reverse("leaderboard")}"', main)

    def test_anonymous_play_button_leads_to_login_through_the_guard(self):
        response = self.client.get(reverse("home"))
        play_url = reverse("play")
        self.assertContains(response, f'href="{play_url}"')
        followed = self.client.get(play_url, follow=True)
        self.assertEqual(
            followed.redirect_chain,
            [(f"{reverse('login')}?next={play_url}", 302)],
        )
        self.assertContains(followed, "<h1>Entrar</h1>", html=True)
