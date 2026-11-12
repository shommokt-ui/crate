"""The first phone response must render without fetching startup resources."""

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as crate
import phone
import phone_page
import settings


class PhonePageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.settings = root / "settings.json"
        settings.save(self.settings, {"phone": True})
        with patch.object(crate, "get_capabilities", return_value={"mp3_available": True}):
            self.app = crate.create_app(root / "jobs", self.settings)
        self.token = phone.load_token(root / "phone-token")
        self.client = self.app.test_client()

    def get_phone(self):
        return self.client.get(f"/?t={self.token}", headers={"Host": "192.168.1.5:8765"},
                               environ_base={"REMOTE_ADDR": "192.168.1.20"})

    def test_phone_startup_is_self_contained_and_nonce_authorized(self):
        response = self.get_phone()
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertNotIn('<script src=', page)
        self.assertNotIn('<link rel="stylesheet"', page)
        self.assertNotIn('src="/static/logo.svg"', page)
        self.assertIn('src="data:image/svg+xml;base64,', page)
        self.assertIn('id="playlist-form"', page)
        self.assertIn('id="phone-startup"', page)
        self.assertIn('<style>:root', page)
        nonces = re.findall(r'<script nonce="([^"]+)"', page)
        self.assertEqual(len(nonces), 7)
        self.assertEqual(len(set(nonces)), 1)
        self.assertIn(f"script-src 'self' 'nonce-{nonces[0]}'", response.headers["Content-Security-Policy"])
        self.assertNotIn("'unsafe-inline'", response.headers["Content-Security-Policy"].split('script-src ')[1].split(';')[0])
        next_nonce = re.search(r'<script nonce="([^"]+)"', self.get_phone().get_data(as_text=True))[1]
        self.assertNotEqual(nonces[0], next_nonce)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_deferred_scripts_run_after_markup_in_dependency_order(self):
        page = self.get_phone().get_data(as_text=True)
        self.assertLess(page.index('window.__crateSettings'), page.index('<body>'))
        self.assertLess(page.index('id="settings-form"'), page.index('const timer = setTimeout(failed'))
        self.assertLess(page.index('const timer = setTimeout(failed'), page.index('window.CrateSettings ='))
        self.assertLess(page.index('window.CrateSettings ='), page.index('const terminalStates ='))

    def test_desktop_keeps_external_assets(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('<script src="/static/theme.js">', page)
        self.assertNotIn('id="phone-startup"', page)

    def test_inline_script_cannot_close_its_own_tag(self):
        static = Path(self.temporary.name)
        (static / 'test.js').write_text('const text = "</ScRiPt><h1>example</h1>";')
        (static / 'phone-startup.js').write_text('')
        (static / 'style.css').write_text('')
        page = phone_page.render('<body><script src="/static/test.js" defer></script></body>', static, 'fixture')
        self.assertIn(r'<\/script>', page)
        self.assertEqual(page.lower().count('</script>'), 2)


if __name__ == '__main__':
    unittest.main()
