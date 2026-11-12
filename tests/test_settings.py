"""Malformed preferences fall back safely without breaking app startup or APIs."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import app as crate
import settings


class SettingsTests(unittest.TestCase):
    def test_choices_reject_wrong_types_and_unhashable_values(self):
        for key in ("theme", "format", "quality"):
            for value in ([], {}, None, True, False, 320.0, "unknown"):
                with self.subTest(key=key, value=value):
                    sanitized = settings.sanitize({key: value, "history": False})
                    self.assertEqual(sanitized[key], settings.DEFAULTS[key])
                    self.assertIs(type(sanitized[key]), type(settings.DEFAULTS[key]))
                    self.assertFalse(sanitized["history"])

    def test_valid_preferences_are_preserved(self):
        chosen = {"theme": "dark", "format": "opus", "quality": 192, "history": False}
        self.assertEqual(settings.sanitize(chosen), {**settings.DEFAULTS, **chosen})

    def test_malformed_saved_preferences_do_not_break_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"theme": [], "format": {}, "quality": [], "visuals": False}))
            self.assertEqual(settings.load(path), {**settings.DEFAULTS, "visuals": False})
            with patch.object(crate, "get_capabilities", return_value={"mp3_available": True}):
                app = crate.create_app(Path(directory) / "jobs", path)
            app.config.update(TESTING=True)
            client = app.test_client()
            self.assertEqual(client.get("/").status_code, 200)
            self.assertEqual(client.get("/api/settings").status_code, 200)

    def test_settings_api_sanitizes_malformed_values_and_preserves_other_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings.save(path, {"history": False, "visuals": False})
            with patch.object(crate, "get_capabilities", return_value={"mp3_available": True}):
                app = crate.create_app(Path(directory) / "jobs", path)
            app.config.update(TESTING=True)
            response = app.test_client().post("/api/settings", json={"theme": [], "format": {}, "quality": []})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {**settings.DEFAULTS, "history": False, "visuals": False})
            self.assertEqual(settings.load(path), response.get_json())


if __name__ == "__main__":
    unittest.main()
