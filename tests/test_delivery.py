import tempfile
import unittest
from pathlib import Path
from unittest import mock

import delivery
import settings


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.song = self.tmp / "001 - A.mp3"
        self.song.write_bytes(b"x")
        home = mock.patch.object(delivery.Path, "home", return_value=self.tmp)
        home.start()
        self.addCleanup(home.stop)

    def test_playlist_gets_subfolder_and_keeps_numbers(self):
        message = delivery.deliver([self.song], "My Mix", str(self.tmp / "out"), single=False)
        self.assertTrue((self.tmp / "out" / "My Mix" / "001 - A.mp3").is_file())
        self.assertIn("1 song", message)

    def test_single_strips_number_and_never_overwrites(self):
        delivery.deliver([self.song], "x", str(self.tmp / "out"), single=True)
        delivery.deliver([self.song], "x", str(self.tmp / "out"), single=True)
        self.assertEqual(sorted(p.name for p in (self.tmp / "out").iterdir()), ["A (2).mp3", "A.mp3"])

    def test_folder_outside_home_falls_back_and_drives_are_allowed(self):
        self.assertEqual(delivery.resolve_folder("/etc"), delivery.FALLBACK)
        self.assertEqual(delivery.resolve_folder("/Volumes/Music/Crate"), Path("/Volumes/Music/Crate"))
        self.assertEqual(delivery.resolve_folder(""), delivery.FALLBACK)

    def test_settings_keep_folder_and_drop_old_keys(self):
        clean = settings.sanitize({"folder": "/Users/x/Music", "destination": "music"})
        self.assertEqual(clean["folder"], "/Users/x/Music")
        self.assertNotIn("destination", clean)
        self.assertEqual(settings.sanitize({})["folder"], "")


if __name__ == "__main__":
    unittest.main()
