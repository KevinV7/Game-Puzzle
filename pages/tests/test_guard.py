from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

PASSWORD = "Sl1de-Tiles-42"
PROTECTED = ["play", "leaderboard"]
PUBLIC = ["home", "instructions", "signup", "login", "healthz"]


class AnonymousGuardTests(TestCase):
    def test_protected_pages_redirect_to_login_preserving_next(self):
        for name in PROTECTED:
            with self.subTest(name):
                url = reverse(name)
                response = self.client.get(url)
                self.assertRedirects(
                    response, f"{reverse('login')}?next={url}", fetch_redirect_response=False
                )

    def test_public_pages_are_not_redirected(self):
        for name in PUBLIC:
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)


class ReturnAfterLoginTests(TestCase):
    def test_login_from_redirect_lands_on_original_target(self):
        get_user_model().objects.create_user("ana", "ana@example.com", PASSWORD)
        url = reverse("play")
        response = self.client.post(
            f"{reverse('login')}?next={url}", {"username": "ana", "password": PASSWORD}
        )
        self.assertRedirects(response, url, fetch_redirect_response=False)


class AuthenticatedAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.ana = User.objects.create_user("ana", "ana@example.com", PASSWORD)
        cls.beto = User.objects.create_user("beto", "beto@example.com", PASSWORD)

    def setUp(self):
        self.client.force_login(self.ana)

    def test_authenticated_user_gets_protected_pages(self):
        for name in PROTECTED:
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_placeholders_state_they_are_unavailable_and_show_no_game_or_scores(self):
        for name in PROTECTED:
            with self.subTest(name):
                response = self.client.get(reverse(name))
                self.assertContains(response, "Aún no disponible")
                for forbidden in ["<canvas", "timer", "temporizador", "dificultad", "<table"]:
                    self.assertNotContains(response, forbidden, msg_prefix=forbidden)

    def test_no_page_exposes_another_users_data(self):
        for name in PROTECTED + PUBLIC:
            with self.subTest(name):
                content = self.client.get(reverse(name)).content.decode()
                self.assertNotIn("beto", content)
                self.assertNotIn("beto@example.com", content)
