from django.test import TestCase
from django.urls import reverse


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
