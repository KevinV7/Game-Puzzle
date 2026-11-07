from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase


class UserModelTests(TestCase):
    def test_custom_user_model_is_configured(self):
        self.assertEqual(get_user_model()._meta.label, "accounts.User")

    def test_email_is_required_on_the_model(self):
        self.assertFalse(get_user_model()._meta.get_field("email").blank)

    def test_duplicate_email_ignoring_case_raises_integrity_error(self):
        User = get_user_model()
        User.objects.create_user(username="ana", email="ana@example.com", password="x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user(username="ana2", email="ANA@example.com", password="x")

    def test_distinct_emails_are_allowed(self):
        User = get_user_model()
        User.objects.create_user(username="ana", email="ana@example.com", password="x")
        User.objects.create_user(username="beto", email="beto@example.com", password="x")
        self.assertEqual(User.objects.count(), 2)
