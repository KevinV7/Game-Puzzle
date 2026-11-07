import importlib.util
import os
import re
import uuid
from pathlib import Path
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

SETTINGS_PATH = Path(__file__).resolve().parent / "settings.py"
ENV_EXAMPLE_PATH = SETTINGS_PATH.parent.parent / ".env.example"


def load_settings(env):
    """Import a fresh copy of the settings module under a patched environment."""
    spec = importlib.util.spec_from_file_location(f"settings_{uuid.uuid4().hex}", SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=True):
        spec.loader.exec_module(module)
    return module


class EnvHelpersTests(SimpleTestCase):
    def setUp(self):
        self.settings_module = load_settings({"DJANGO_DEBUG": "1"})

    def test_env_str_returns_default_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.settings_module.env_str("MISSING", "fallback"), "fallback")

    def test_env_str_reads_value(self):
        with mock.patch.dict(os.environ, {"SOME_KEY": "value"}, clear=True):
            self.assertEqual(self.settings_module.env_str("SOME_KEY", "fallback"), "value")

    def test_env_bool_parses_truthy_and_falsy_values(self):
        for raw, expected in [("1", True), ("true", True), ("YES", True), ("0", False), ("no", False), ("", False)]:
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"FLAG": raw}, clear=True):
                self.assertIs(self.settings_module.env_bool("FLAG", not expected), expected)

    def test_env_bool_default_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIs(self.settings_module.env_bool("FLAG", True), True)
            self.assertIs(self.settings_module.env_bool("FLAG", False), False)

    def test_env_list_splits_and_strips(self):
        with mock.patch.dict(os.environ, {"ITEMS": " a, b ,,c "}, clear=True):
            self.assertEqual(self.settings_module.env_list("ITEMS"), ["a", "b", "c"])

    def test_env_list_default_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.settings_module.env_list("ITEMS", ["x"]), ["x"])


class SettingsTests(SimpleTestCase):
    def test_missing_secret_key_fails_when_debug_is_off(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "DJANGO_SECRET_KEY"):
            load_settings({"DJANGO_DEBUG": "0"})

    def test_secret_key_is_read_from_environment(self):
        module = load_settings({"DJANGO_DEBUG": "0", "DJANGO_SECRET_KEY": "s3cret"})
        self.assertEqual(module.SECRET_KEY, "s3cret")
        self.assertFalse(module.DEBUG)

    def test_debug_allows_missing_secret_key(self):
        module = load_settings({"DJANGO_DEBUG": "1"})
        self.assertTrue(module.SECRET_KEY)

    def test_every_env_variable_read_by_settings_is_in_env_example(self):
        self.assertTrue(ENV_EXAMPLE_PATH.is_file(), ".env.example is missing from the project root")
        read = set(re.findall(r'env_(?:str|bool|list)\(\s*"([A-Z0-9_]+)"', SETTINGS_PATH.read_text()))
        listed = {
            line.split("=", 1)[0]
            for line in ENV_EXAMPLE_PATH.read_text().splitlines()
            if "=" in line and not line.startswith("#")
        }
        self.assertTrue(read)
        self.assertEqual(sorted(read - listed), [])


class HealthzTests(TestCase):
    def test_healthz_is_public_and_returns_200(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_healthz_url_is_stable(self):
        self.assertEqual(reverse("healthz"), "/healthz/")

    def test_healthz_returns_503_when_database_is_down(self):
        with mock.patch("config.health.connection.cursor", side_effect=OperationalError("down")):
            response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 503)
