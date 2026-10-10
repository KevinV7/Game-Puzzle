from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

PASSWORD = "Sl1de-Tiles-42"


def is_logged_in(client):
    return "_auth_user_id" in client.session


class AccountUrlTests(TestCase):
    def test_account_urls_are_stable_spanish_slugs(self):
        self.assertEqual(reverse("signup"), "/cuentas/registro/")
        self.assertEqual(reverse("login"), "/cuentas/entrar/")
        self.assertEqual(reverse("logout"), "/cuentas/salir/")


class SignupViewTests(TestCase):
    def signup_data(self, **overrides):
        data = {
            "username": "ana",
            "email": "ana@example.com",
            "password1": PASSWORD,
            "password2": PASSWORD,
        }
        data.update(overrides)
        return data

    def test_signup_page_is_public(self):
        response = self.client.get(reverse("signup"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/signup.html")

    def test_valid_signup_creates_user_and_logs_in(self):
        response = self.client.post(reverse("signup"), self.signup_data())
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        user = get_user_model().objects.get(username="ana")
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))

    def test_invalid_signup_creates_no_user_and_no_session(self):
        response = self.client.post(reverse("signup"), self.signup_data(password1="password", password2="password"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertFalse(is_logged_in(self.client))

    def test_duplicate_email_shows_error(self):
        get_user_model().objects.create_user("beto", "ana@example.com", PASSWORD)
        response = self.client.post(reverse("signup"), self.signup_data(email="ANA@example.com"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertEqual(get_user_model().objects.count(), 1)


class LoginViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("ana", "ana@example.com", PASSWORD)

    def test_login_page_is_public(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/login.html")

    def test_valid_login_authenticates_and_redirects_home(self):
        response = self.client.post(reverse("login"), {"username": "ana", "password": PASSWORD})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertEqual(self.client.session["_auth_user_id"], str(self.user.pk))

    def test_invalid_login_rerenders_with_error_and_no_session(self):
        response = self.client.post(reverse("login"), {"username": "ana", "password": "wrong-pass"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertFalse(is_logged_in(self.client))


class LogoutViewTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("ana", "ana@example.com", PASSWORD)
        self.client.force_login(user)

    def test_post_logout_ends_session_and_redirects(self):
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertFalse(is_logged_in(self.client))

    def test_get_logout_is_rejected_and_keeps_session(self):
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(is_logged_in(self.client))
