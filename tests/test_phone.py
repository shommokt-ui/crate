import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as crate
import phone
import settings as user_settings


class PhoneAccessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "jobs"
        self.settings = Path(self.temporary.name) / "settings.json"
        with patch.object(crate, "get_capabilities", return_value={"mp3_available": True}):
            self.app = crate.create_app(self.directory, self.settings)
        self.mac = self.app.test_client()
        self.phone = self.app.test_client()
        self.base = {"REMOTE_ADDR": "192.168.1.20"}
        self.host = {"Host": "192.168.1.5:8765"}

    def tearDown(self):
        self.temporary.cleanup()

    def get(self, path, **kwargs):
        return self.phone.get(path, headers=self.host, environ_base=self.base, **kwargs)

    def turn_on(self):
        user_settings.save(self.settings, {"phone": True})
        return phone.load_token(self.settings.with_name("phone-token"))

    def test_phone_is_refused_while_switched_off(self):
        self.assertEqual(self.get("/").status_code, 403)

    def test_refused_phone_sees_a_page_but_the_api_stays_json(self):
        page = self.get("/")
        self.assertEqual(page.mimetype, "text/html")
        self.assertIn("Phone access is off", page.get_data(as_text=True))
        self.turn_on()
        self.assertIn("scan the code", self.get("/?t=old").get_data(as_text=True))
        self.assertIn("error", self.get("/api/settings").get_json())

    def test_phone_page_is_compressed_when_safari_asks(self):
        import gzip
        token = self.turn_on()
        response = self.phone.get(f"/?t={token}", headers={**self.host, "Accept-Encoding": "gzip, deflate"}, environ_base=self.base)
        self.assertEqual(response.headers["Content-Encoding"], "gzip")
        self.assertIn("Opening Crate", gzip.decompress(response.get_data()).decode())
        self.assertLess(len(response.get_data()), 40000)
        self.assertNotIn("Content-Encoding", self.mac.get("/").headers)

    def test_phone_needs_the_code(self):
        token = self.turn_on()
        self.assertEqual(self.get("/").status_code, 403)
        self.assertEqual(self.get("/?t=wrong").status_code, 403)
        ok = self.get(f"/?t={token}")
        self.assertEqual(ok.status_code, 200)
        self.assertIn("crate_phone=", ok.headers["Set-Cookie"])
        self.assertEqual(self.get("/api/settings").status_code, 200)  # the cookie now carries it

    def test_icon_is_public_but_scripts_are_not(self):
        self.turn_on()
        with self.get("/static/apple-touch-icon.png") as response:
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get("/static/app.js").status_code, 403)

    def test_fresh_code_replaces_cookie_after_reset(self):
        old_token = self.turn_on()
        self.assertEqual(self.get(f"/?t={old_token}").status_code, 200)
        self.mac.post("/api/phone/reset", json={})
        self.assertEqual(self.get("/api/settings").status_code, 403)
        self.assertEqual(self.get(f"/?t={old_token}").status_code, 403)
        new_token = phone.load_token(self.settings.with_name("phone-token"))
        response = self.get(f"/?t={new_token}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"crate_phone={new_token}", response.headers["Set-Cookie"])
        self.assertEqual(self.get("/api/settings").status_code, 200)

    def test_public_asset_with_wrong_code_does_not_grant_access(self):
        self.turn_on()
        for asset in ("logo.svg", "manifest.webmanifest", "apple-touch-icon.png"):
            with self.subTest(asset=asset):
                with self.get(f"/static/{asset}?t=wrong") as response:
                    self.assertEqual(response.status_code, 200)
                    self.assertNotIn("Set-Cookie", response.headers)
                self.assertEqual(self.get("/api/settings").status_code, 403)
                self.assertEqual(self.get("/static/app.js").status_code, 403)

    def test_valid_cookie_survives_stale_bookmarked_code(self):
        token = self.turn_on()
        self.get(f"/?t={token}")
        response = self.get("/?t=old")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Set-Cookie", response.headers)

    def test_phone_cannot_reset_pairing(self):
        token = self.turn_on()
        self.get(f"/?t={token}")
        response = self.phone.post("/api/phone/reset", json={}, headers=self.host, environ_base=self.base)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(phone.load_token(self.settings.with_name("phone-token")), token)

    def test_phone_cannot_use_a_public_hostname(self):
        token = self.turn_on()
        response = self.phone.get(f"/?t={token}", headers={"Host": "evil.example.com"}, environ_base=self.base)
        self.assertEqual(response.status_code, 403)

    def test_phone_cannot_see_pairing_or_change_mac_settings(self):
        token = self.turn_on()
        self.get(f"/?t={token}")
        self.assertEqual(self.get("/api/phone").status_code, 403)
        self.phone.post("/api/settings", json={"phone": False, "folder": "/x", "theme": "dark"},
                        headers=self.host, environ_base=self.base)
        saved = user_settings.load(self.settings)
        self.assertTrue(saved["phone"])
        self.assertEqual(saved["folder"], "")
        self.assertEqual(saved["theme"], "dark")

    def test_mac_pairing_info_and_reset(self):
        self.assertFalse(self.mac.get("/api/phone").get_json()["enabled"])
        token = self.turn_on()
        with patch.object(phone, "lan_ip", return_value="192.168.1.5"):
            info = self.mac.get("/api/phone").get_json()
            self.assertIn(f"192.168.1.5:8765/?t={token}", info["url"])
            self.assertIn('<svg xmlns="http://www.w3.org/2000/svg"', info["svg"])  # without it the code renders blank
            self.mac.post("/api/phone/reset", json={})
            self.assertNotEqual(phone.load_token(self.settings.with_name("phone-token")), token)

    def test_host_rules(self):
        self.assertTrue(phone.host_is_local_network("192.168.0.4"))
        self.assertTrue(phone.host_is_local_network("my-mac.local"))
        self.assertFalse(phone.host_is_local_network("8.8.8.8"))
        self.assertFalse(phone.host_is_local_network("example.com"))


if __name__ == "__main__":
    unittest.main()
