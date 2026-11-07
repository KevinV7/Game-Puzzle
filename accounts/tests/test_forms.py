from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.forms import SignupForm

STRONG_PASSWORD = "Sl1de-Tiles-42"


def form_data(**overrides):
    data = {
        "username": "ana",
        "email": "ana@example.com",
        "password1": STRONG_PASSWORD,
        "password2": STRONG_PASSWORD,
    }
    data.update(overrides)
    return data


class SignupFormTests(TestCase):
    def test_valid_data_creates_user_with_hashed_password(self):
        form = SignupForm(form_data())
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(user.username, "ana")
        self.assertNotEqual(user.password, STRONG_PASSWORD)
        self.assertTrue(user.check_password(STRONG_PASSWORD))

    def test_email_is_normalized_to_lowercase_and_trimmed(self):
        form = SignupForm(form_data(email="  Ana@Example.COM "))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().email, "ana@example.com")

    def test_duplicate_username_is_rejected(self):
        get_user_model().objects.create_user("ana", "other@example.com", STRONG_PASSWORD)
        form = SignupForm(form_data())
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_duplicate_email_is_rejected_ignoring_case(self):
        get_user_model().objects.create_user("beto", "ana@example.com", STRONG_PASSWORD)
        form = SignupForm(form_data(email="ANA@example.com"))
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_invalid_email_is_rejected(self):
        form = SignupForm(form_data(email="not-an-email"))
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_missing_email_is_rejected(self):
        form = SignupForm(form_data(email=""))
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)

    def test_weak_passwords_are_rejected(self):
        for label, password in [
            ("common", "password"),
            ("numeric", "73920184651"),
            ("short", "Ab1!"),
        ]:
            with self.subTest(label):
                form = SignupForm(form_data(password1=password, password2=password))
                self.assertFalse(form.is_valid())
                self.assertIn("password2", form.errors)
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_mismatched_passwords_are_rejected(self):
        form = SignupForm(form_data(password2="Different-Pass-42"))
        self.assertFalse(form.is_valid())
        self.assertIn("password2", form.errors)
