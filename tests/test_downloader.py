import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import downloader


class URLTests(unittest.TestCase):
    def test_normalizes_a_public_playlist(self):
        self.assertEqual(
            downloader.validate_playlist_url(" http://m.soundcloud.com/artist/sets/my-set/?si=123&utm_source=share "),
            "https://soundcloud.com/artist/sets/my-set",
        )

    def test_rejects_other_hosts_private_links_and_ambiguous_paths(self):
        bad = [
            "https://example.com/a/sets/b", "https://soundcloud.com.evil.test/a/sets/b",
            "https://user:something@soundcloud.com/a/sets/b", "https://soundcloud.com:443/a/sets/b",
            "https://soundcloud.com/a/sets/b#x", "https://soundcloud.com/a/sets/b#", "https://soundcloud.com/a/sets/b/private-token",
            "https://soundcloud.com/a/sets/b?secret_token=s-private", "https://soundcloud.com/a/sets/b?secret_token=", "file:///a/sets/b",
            "https://soundcloud.com/a/song", "https://soundcloud.com/a/sets/../b",
            "https://soundcloud.com/a/sets/%2e%2e", "https://soundcloud.com/a/sets/b\nignored",
        ]
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ValueError):
                downloader.validate_playlist_url(url)

    def test_accepts_extractor_numeric_tracks_but_no_remote_targets(self):
        self.assertEqual(downloader._track_url("https://api.soundcloud.com/tracks/soundcloud%3Atracks%3A123"),
                         "https://api-v2.soundcloud.com/tracks/123")
        for url in ["https://localhost/music", "https://soundcloud.com/a/sets/b", "https://soundcloud.com/a/tracks",
                    "https://api-v2.soundcloud.com/tracks/123?secret_token=s-private"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                downloader._track_url(url)


class MetadataTests(unittest.TestCase):
    def test_bulk_metadata_keeps_order_and_unavailable_entries(self):
        extractor = downloader._PlaylistMetadataIE()
        playlist = {
            "id": 9, "title": "Set", "user": {"username": "Curator"}, "tracks": [
                {"id": 2}, {"id": 1, "title": "Known", "permalink_url": "https://soundcloud.com/a/known",
                           "duration": 123000, "user": {"username": "Artist"}},
                {"id": 3, "title": "Preview", "policy": "SNIP"},
            ],
        }
        with patch.object(extractor, "_call_api", return_value=[
            {"id": 2, "title": "Resolved", "duration": 4000, "user": {"username": "Other"}},
        ]) as api:
            result = extractor._extract_set(playlist)
        self.assertEqual([entry["id"] for entry in result["entries"]], ["2", "1", "3"])
        self.assertEqual(result["entries"][0]["title"], "Resolved")
        self.assertEqual(result["entries"][1]["duration"], 123)
        self.assertEqual(result["entries"][2]["availability"], "unavailable")
        self.assertEqual(api.call_args.kwargs["query"], {"ids": "2"})

    def test_rejects_oversized_playlist_before_fetching_tracks(self):
        extractor = downloader._PlaylistMetadataIE()
        with patch.object(extractor, "_call_api") as api, self.assertRaises(ValueError):
            extractor._extract_set({"id": 9, "track_count": 501, "tracks": [{"id": 1}]})
        api.assert_not_called()

    def test_preview_distinguishes_known_protected_formats_from_unknown_media(self):
        drm = {"format": {"protocol": "ctr-hls"}}
        alternate_drm = {"format": {"protocol": "cbc-hls"}}
        public = {"format": {"protocol": "hls"}}
        self.assertFalse(downloader._raw_track_available({"media": {"transcodings": [drm, alternate_drm]}}))
        self.assertTrue(downloader._raw_track_available({"media": {"transcodings": [drm, public]}}))
        self.assertTrue(downloader._raw_track_available({"id": 1}))
        self.assertTrue(downloader._raw_track_available({"downloadable": True, "media": {"transcodings": [drm]}}))
        self.assertFalse(downloader._raw_track_available({"media": {"transcodings": [{**public, "snipped": True}]}}))

    def test_filters_preview_and_protected_formats(self):
        full = {"format_id": "http_mp3", "url": "https://cdn.example/full"}
        formats = [full, {"format_id": "hls_mp3_preview"}, {"format_id": "premium", "has_drm": True},
                   {"format_id": "stream", "snipped": True}]
        self.assertEqual(downloader._full_audio_formats({"formats": formats}), [full])
        self.assertEqual(downloader._full_audio_formats({"formats": [full], "policy": "SNIP"}), [])


class FakeYDL:
    options_seen = []
    fail_ids = set()
    cancellation = None

    def __init__(self, options):
        self.options = options
        self.options_seen.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def extract_info(self, url, **kwargs):
        track_id = url.rsplit("/", 1)[-1]
        if track_id in self.fail_ids:
            raise RuntimeError("Track unavailable")
        return {"id": track_id, "title": "Actual title", "formats": [{"format_id": "http_mp3", "ext": "mp3"}]}

    def process_ie_result(self, info, download=True):
        template = self.options["outtmpl"]
        # Use yt-dlp's own template formatting semantics for escaped percent signs.
        path = Path(template % {"ext": "mp3"})
        path.write_bytes(b"mock audio bytes")
        if self.cancellation:
            self.cancellation.set()
        for hook in self.options["progress_hooks"]:
            hook({"downloaded_bytes": 16, "total_bytes": 16, "status": "finished"})
        return {**info, "filepath": str(path)}


def example_preview():
    return {
        "url": "https://soundcloud.com/artist/sets/my-set", "title": "../Set 100%", "creator": "Curator",
        "tracks": [
            {"id": str(i), "title": "../Same: song 100%", "artist": "A/B", "duration": 123,
             "url": f"https://api-v2.soundcloud.com/tracks/{i}", "available": True}
            for i in range(1, 4)
        ],
    }


class DownloadTests(unittest.TestCase):
    def setUp(self):
        FakeYDL.options_seen = []
        FakeYDL.fail_ids = set()
        FakeYDL.cancellation = None
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name).resolve()
        self.patch_ydl = patch.object(downloader.yt_dlp, "YoutubeDL", FakeYDL)
        self.patch_ydl.start()
        self.addCleanup(self.patch_ydl.stop)
        self.patch_ffmpeg = patch.object(downloader, "_ffmpeg_path", return_value="/ffmpeg")
        self.patch_ffmpeg.start()
        self.addCleanup(self.patch_ffmpeg.stop)

    def test_one_zip_unique_safe_ordered_names_and_failure_report(self):
        FakeYDL.fail_ids = {"2"}
        updates = []
        output = downloader.download_playlist(example_preview(), self.path, "original", updates.append, threading.Event())
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            audio = [name for name in names if name.endswith(".mp3")]
            self.assertEqual(len(audio), 2)
            self.assertTrue(audio[0].startswith("001 - "))
            self.assertTrue(audio[1].startswith("003 - "))
            self.assertTrue(all("/" not in name and "\\" not in name for name in names))
            self.assertEqual(len(names), len(set(names)))
            manifest = json.loads(archive.read("playlist.json"))
            self.assertEqual(manifest["completed_tracks"], 2)
            self.assertEqual(manifest["failed_tracks"], 1)
            self.assertIn("skipped-tracks.txt", names)
        self.assertEqual(list(self.path.iterdir()), [output])
        self.assertEqual(updates[-1]["status"], "packing")
        self.assertEqual(updates[-1]["tracks"][0]["duration"], 123)

    def test_cancellation_removes_partial_files_and_archive(self):
        event = threading.Event()
        FakeYDL.cancellation = event
        with self.assertRaises(downloader.DownloadCancelled):
            downloader.download_playlist(example_preview(), self.path, "original", lambda _: None, event)
        self.assertEqual(list(self.path.iterdir()), [])

    def test_all_failures_do_not_return_an_empty_zip(self):
        FakeYDL.fail_ids = {"1", "2", "3"}
        with self.assertRaisesRegex(RuntimeError, "No tracks"):
            downloader.download_playlist(example_preview(), self.path, "original", lambda _: None, threading.Event())
        self.assertEqual(list(self.path.iterdir()), [])

    def test_mp3_uses_bundled_ffmpeg(self):
        downloader.download_playlist(example_preview(), self.path, "mp3", lambda _: None, threading.Event())
        self.assertTrue(all(options["ffmpeg_location"] == "/ffmpeg" for options in FakeYDL.options_seen))
        self.assertEqual(FakeYDL.options_seen[0]["postprocessors"][0]["preferredcodec"], "mp3")

    def test_untrusted_track_target_is_not_extracted(self):
        preview = example_preview()
        preview["tracks"] = [{**preview["tracks"][0], "url": "http://127.0.0.1/private"}]
        with self.assertRaises(RuntimeError):
            downloader.download_playlist(preview, self.path, "original", lambda _: None, threading.Event())
        self.assertEqual(FakeYDL.options_seen, [])


if __name__ == "__main__":
    unittest.main()
