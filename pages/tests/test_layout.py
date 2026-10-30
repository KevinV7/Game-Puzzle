from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

PASSWORD = "Sl1de-Tiles-42"
LOGOUT_FORM_OPEN = '<form method="post" action="/cuentas/salir/"'


class BaseLayoutTests(TestCase):
    def test_every_page_declares_the_viewport_meta(self):
        for name in ["login", "signup"]:
            with self.subTest(name):
                response = self.client.get(reverse(name))
                self.assertContains(response, '<meta name="viewport" content="width=device-width, initial-scale=1">')

    def test_pages_link_the_site_stylesheet(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "/static/css/site.css")

    def test_pages_declare_an_inline_icon_so_browsers_do_not_request_favicon_ico(self):
        for name in ["login", "signup"]:
            with self.subTest(name):
                response = self.client.get(reverse(name))
                self.assertContains(response, '<link rel="icon" href="data:image/svg+xml,')
                self.assertNotContains(response, "/favicon.ico")

    def test_anonymous_navigation_shows_login_and_signup_but_no_logout(self):
        response = self.client.get(reverse("login"))
        for name in ["home", "instructions", "play", "leaderboard", "login", "signup"]:
            self.assertContains(response, f'href="{reverse(name)}"')
        self.assertNotContains(response, LOGOUT_FORM_OPEN)

    def test_authenticated_navigation_shows_post_logout_form_and_own_name(self):
        user = get_user_model().objects.create_user("ana", "ana@example.com", PASSWORD)
        self.client.force_login(user)
        response = self.client.get(reverse("play"))
        self.assertContains(response, LOGOUT_FORM_OPEN)
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "ana")
        self.assertNotContains(response, f'href="{reverse("login")}"')
        self.assertNotContains(response, f'href="{reverse("signup")}"')
