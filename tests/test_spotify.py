"""Spotify playlist reading and SoundCloud matching, without network access."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

import app as crate
import spotify

PLAYLIST_ID = "37i9dQZF1DXcBWIGoYBM5M"
PLAYLIST_URL = f"https://open.spotify.com/playlist/{PLAYLIST_ID}"
ENTITY = {
    "title": "Road Trip", "subtitle": "A Friend",
    "coverArt": {"sources": [{"url": "https://i.scdn.co/image/abc"}]},
    "trackList": [{"uri": "spotify:track:abc", "title": "Stop The Wedding!", "subtitle": "Ashe", "duration": 198840}],
}


class SpotifyUrlTests(unittest.TestCase):
    def test_accepts_playlist_links_and_strips_tracking(self):
        for url in (PLAYLIST_URL, PLAYLIST_URL + "?si=abc", f"https://open.spotify.com/intl-de/playlist/{PLAYLIST_ID}"):
            self.assertEqual(spotify.validate_spotify_url(url), PLAYLIST_URL)

    def test_rejects_other_links(self):
        for url in ("https://soundcloud.com/a/sets/b", "https://open.spotify.com/track/" + PLAYLIST_ID,
                    "https://evil.example/playlist/" + PLAYLIST_ID, "https://open.spotify.com/playlist/short"):
            with self.assertRaises(ValueError):
                spotify.validate_spotify_url(url)


class SpotifyPreviewTests(unittest.TestCase):
    def test_preview_shape(self):
        with patch.object(spotify, "_fetch_entity", return_value=copy.deepcopy(ENTITY)):
            result = spotify.preview_spotify(PLAYLIST_URL)
        self.assertEqual(result["source"], "spotify")
        self.assertEqual(result["tracks"][0], {
            "id": "abc", "title": "Stop The Wedding!", "artist": "Ashe", "duration": 198.84, "url": "", "available": True})
        self.assertEqual(result["artwork"], "https://i.scdn.co/image/abc")


class MatchingTests(unittest.TestCase):
    def score(self, candidate):
        return spotify._score(candidate, "Stop The Wedding!", "Ashe", 198.8)

    def test_official_upload_beats_variants(self):
        official = {"title": "Stop The Wedding!", "uploader": "Ashe", "duration": 198.9}
        self.assertGreaterEqual(self.score(official), spotify.MIN_SCORE)
        for title, length in (("Stop The Wedding! (Slowed)", 198.9), ("Stop The Wedding! (Sped Up)", 140.0),
                              ("Ashe - Stop The Wedding (Remix)", 198.9), ("Stop The Wedding!", 67.0)):
            self.assertLess(self.score({"title": title, "uploader": "someone", "duration": length}), spotify.MIN_SCORE)

    def test_feature_credits_are_ignored(self):
        score = spotify._score({"title": "expectations", "uploader": "Olivia Rodrigo", "duration": 150},
                               "expectations (feat. Chappell Roan)", "Olivia Rodrigo, Chappell Roan", 150)
        self.assertGreaterEqual(score, spotify.MIN_SCORE)

    def test_no_match_raises(self):
        with patch.object(spotify.yt_dlp, "YoutubeDL") as ydl:
            ydl.return_value.__enter__.return_value.extract_info.return_value = {"entries": []}
            with self.assertRaises(ValueError):
                spotify.find_on_soundcloud({"title": "x", "artist": "y", "duration": 100})


class SpotifyEndpointTests(unittest.TestCase):
    def test_preview_routes_by_source(self):
        client = crate.create_app().test_client()
        with patch.object(crate, "preview_spotify", return_value={"source": "spotify", "url": PLAYLIST_URL,
                                                                  "title": "T", "tracks": [{"id": "1", "title": "a"}]}) as reader:
            response = client.post("/api/preview", json={"url": PLAYLIST_URL, "source": "spotify"})
        self.assertEqual(response.status_code, 200)
        reader.assert_called_once_with(PLAYLIST_URL)
        # No source field: the Spotify host alone selects the Spotify reader.
        with patch.object(crate, "preview_spotify", return_value={"source": "spotify", "url": PLAYLIST_URL,
                                                                  "title": "T", "tracks": [{"id": "1", "title": "a"}]}) as reader:
            self.assertEqual(client.post("/api/preview", json={"url": PLAYLIST_URL}).status_code, 200)
        reader.assert_called_once()


if __name__ == "__main__":
    unittest.main()


class SingleSongTests(unittest.TestCase):
    def test_links_are_classified(self):
        from downloader import classify_soundcloud_url
        self.assertEqual(classify_soundcloud_url("https://soundcloud.com/a/sets/b?si=1"), ("playlist", "https://soundcloud.com/a/sets/b"))
        self.assertEqual(classify_soundcloud_url("https://soundcloud.com/a/song?si=1"), ("track", "https://soundcloud.com/a/song"))
        with self.assertRaises(ValueError):
            classify_soundcloud_url("https://soundcloud.com/a")
        self.assertEqual(spotify.validate_spotify_link(f"https://open.spotify.com/track/{PLAYLIST_ID}?si=x"),
                         ("track", f"https://open.spotify.com/track/{PLAYLIST_ID}"))

    def test_song_endpoint_and_single_file_download(self):
        import tempfile, threading
        from pathlib import Path
        import downloader
        client = crate.create_app(Path(tempfile.mkdtemp())).test_client()
        preview = {"kind": "track", "url": "https://soundcloud.com/a/song", "title": "Song", "creator": "A", "track_count": 1,
                   "tracks": [{"id": "1", "title": "Song", "artist": "A", "url": "https://soundcloud.com/a/song", "available": True}]}
        with patch.object(crate, "preview_track", return_value=preview):
            got = client.post("/api/preview", json={"url": "https://soundcloud.com/a/song"})
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.get_json()["kind"], "track")

        def fake_download(options_self, url, **kwargs):
            raise AssertionError("unexpected")
        work = Path(tempfile.mkdtemp())

        class FakeYDL:
            def __init__(self, options): self.options = options
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def extract_info(self, *a, **k): return {"_type": "video", "formats": [{"format_id": "http_mp3", "url": "https://x"}]}
            def process_ie_result(self, info, download=True):
                (Path(self.options["outtmpl"].replace("%%", "%").rsplit(".%(ext)s", 1)[0] + ".mp3")).write_bytes(b"audio")

        with patch.object(downloader.yt_dlp, "YoutubeDL", FakeYDL):
            result = downloader.download_playlist(preview, work / "job", "original", lambda v: None, threading.Event())
        self.assertEqual(result.suffix, ".mp3")
        self.assertEqual(result.read_bytes(), b"audio")


class RestrictionReasonTests(unittest.TestCase):
    def test_reasons_are_specific(self):
        from downloader import restriction_reason
        self.assertEqual(restriction_reason({"sharing": "private"}), "Private track")
        self.assertEqual(restriction_reason({"policy": "BLOCK"}), "Blocked in your region")
        self.assertEqual(restriction_reason({"policy": "SNIP"}), "Only a 30-second preview is available")
        drm = {"media": {"transcodings": [{"format": {"protocol": "ctr-encrypted-hls"}, "url": "x"}]}}
        self.assertEqual(restriction_reason(drm), "Locked by SoundCloud")
        self.assertIsNone(restriction_reason({"policy": "ALLOW", "media": {"transcodings": [{"format": {"protocol": "hls"}, "url": "x"}]}}))


class SettingsTests(unittest.TestCase):
    def test_sanitize_drops_bad_values(self):
        import settings
        clean = settings.sanitize({"theme": "neon", "accent": "teal", "quality": 320, "visuals": "yes", "junk": 1, "format": "original"})
        self.assertEqual((clean["theme"], "accent" in clean, clean["quality"], clean["visuals"], clean["format"]),
                         ("system", False, 320, True, "original"))
        self.assertNotIn("junk", clean)

    def test_endpoint_round_trip_and_meta_injection(self):
        import tempfile
        from pathlib import Path
        folder = Path(tempfile.mkdtemp())
        client = crate.create_app(folder / "dl", folder / "settings.json").test_client()
        self.assertEqual(client.get("/api/settings").get_json()["theme"], "system")
        saved = client.post("/api/settings", json={"theme": "dark", "quality": 999}).get_json()
        self.assertEqual((saved["theme"], "accent" in saved, saved["quality"]), ("dark", False, 320))
        self.assertIn("&quot;theme&quot;: &quot;dark&quot;", client.get("/").get_data(as_text=True))
        self.assertEqual(client.post("/api/settings", json=[1]).status_code, 400)


class FormatTests(unittest.TestCase):
    def test_every_conversion_has_matching_extension_and_is_accepted(self):
        import downloader
        self.assertEqual(set(downloader.CONVERSIONS), {"mp3", "aac", "opus", "flac", "wav"})
        for fmt, (_, extension, lossy, cover) in downloader.CONVERSIONS.items():
            self.assertTrue(extension.startswith("."))
            self.assertIn(fmt, downloader.AUDIO_FORMATS)
        self.assertFalse(downloader.CONVERSIONS["wav"][3])   # WAV cannot carry cover art
        self.assertFalse(downloader.CONVERSIONS["flac"][2])  # lossless: no bitrate setting

    def test_all_formats_pass_settings_validation(self):
        import settings
        for fmt in ("mp3", "aac", "opus", "flac", "wav", "original"):
            self.assertEqual(settings.sanitize({"format": fmt})["format"], fmt)
        self.assertEqual(settings.sanitize({"format": "midi"})["format"], "mp3")


class RelaxedMatchTests(unittest.TestCase):
    def search(self, entries, track):
        with patch.object(spotify.yt_dlp, "YoutubeDL") as ydl:
            ydl.return_value.__enter__.return_value.extract_info.return_value = {"entries": entries}
            return spotify.find_on_soundcloud(track)

    def test_extended_cut_is_used_when_no_same_length_upload_exists(self):
        entry = {"title": "Slow Burner", "uploader": "Interplanetary Criminal", "duration": 320.9,
                 "webpage_url": "https://soundcloud.com/interplanetary-criminal/slow-burner-extended"}
        urls, note = self.search([entry], {"title": "Slow Burner", "artist": "Interplanetary Criminal, Original Koffee", "duration": 200.0})
        self.assertEqual(urls, ["https://soundcloud.com/interplanetary-criminal/slow-burner-extended"])
        self.assertIn("different length", note)

    def test_exact_length_match_has_no_note_and_snippets_are_rejected(self):
        exact = {"title": "Slow Burner", "uploader": "Interplanetary Criminal", "duration": 201.0, "webpage_url": "https://soundcloud.com/a/exact"}
        snippet = {"title": "Slow Burner", "uploader": "Interplanetary Criminal", "duration": 30.0, "webpage_url": "https://soundcloud.com/a/snip"}
        urls, note = self.search([snippet, exact], {"title": "Slow Burner", "artist": "Interplanetary Criminal", "duration": 200.0})
        self.assertEqual((urls, note), (["https://soundcloud.com/a/exact"], None))
        with self.assertRaises(ValueError):
            self.search([snippet], {"title": "Slow Burner", "artist": "Interplanetary Criminal", "duration": 200.0})


class ReuploadFallbackTests(unittest.TestCase):
    LOCKED = {"id": "1", "title": "Let You Go", "artist": "Jeremy Sylvester", "duration": 284.0,
              "url": "https://soundcloud.com/jeremysylvester/let-you-go-1", "available": False,
              "reason": "Locked by SoundCloud"}

    def run_job(self, found):
        import tempfile, threading
        from pathlib import Path
        import downloader

        class FakeYDL:
            def __init__(self, options): self.options = options
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def extract_info(self, url, **k): return {"_type": "video", "formats": [{"format_id": "http", "url": "https://x"}]}
            def process_ie_result(self, info, download=True):
                Path(self.options["outtmpl"].replace("%%", "%").rsplit(".%(ext)s", 1)[0] + ".mp3").write_bytes(b"audio")

        preview = {"url": "https://soundcloud.com/a/sets/b", "title": "Mix", "tracks": [dict(self.LOCKED)]}
        rows = []
        calls = []
        def finder(track, exclude=None, strict_only=False):
            calls.append((exclude, strict_only))
            if found is None:
                raise ValueError("No match found")
            return found, None
        def no_youtube(track):
            raise ValueError("No match found")
        with patch.object(downloader.yt_dlp, "YoutubeDL", FakeYDL), patch.object(spotify, "find_on_soundcloud", finder), \
                patch.object(spotify, "find_on_youtube", no_youtube):
            try:
                result = downloader.download_playlist(preview, Path(tempfile.mkdtemp()) / "job", "original", rows.append, threading.Event())
            except RuntimeError as error:
                return None, rows, calls, str(error)
        return result, rows, calls, None

    def test_open_reupload_is_used_for_a_locked_track(self):
        result, rows, calls, _ = self.run_job(["https://soundcloud.com/someone/let-you-go-full"])
        self.assertIsNotNone(result)
        self.assertEqual(calls, [("https://soundcloud.com/jeremysylvester/let-you-go-1", True)])  # original excluded, same-length only
        self.assertEqual(rows[-1]["tracks"][0]["note"], "Used another upload of this song")
        self.assertEqual(rows[-1]["tracks"][0]["status"], "complete")

    def test_no_reupload_keeps_the_original_reason(self):
        result, rows, calls, error = self.run_job(None)
        self.assertIsNone(result)
        self.assertIn("Locked by SoundCloud", error)
        self.assertIn("No other copy found", error)


class HistoryTests(unittest.TestCase):
    def make(self):
        import tempfile
        from pathlib import Path
        folder = Path(tempfile.mkdtemp())
        return crate.create_app(folder / "dl", folder / "settings.json"), folder

    def finish_a_download(self, app):
        import zipfile
        from pathlib import Path
        client = app.test_client()
        preview = {"url": "https://soundcloud.com/a/sets/b", "title": "Late Night", "creator": "Nova", "artwork": "https://i1.sndcdn.com/a.jpg",
                   "track_count": 1, "tracks": [{"id": "1", "title": "x", "artist": "y", "url": "https://soundcloud.com/a/x", "available": True}]}

        def fake_download(data, directory, fmt, update, event, **_):
            update({"completed_tracks": 3, "failed_tracks": 1, "total_tracks": 4})
            archive = Path(directory) / "Late Night.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("a.mp3", b"x")
            return archive

        with patch.object(crate, "preview_playlist", return_value=preview), patch.object(crate, "download_playlist", fake_download):
            pid = client.post("/api/preview", json={"url": "https://soundcloud.com/a/sets/b"}).get_json()["id"]
            job = client.post("/api/jobs", json={"preview_id": pid, "format": "flac"}).get_json()["id"]
            app.extensions["jobs"].threads[job].join(timeout=5)
        return client

    def test_completed_download_is_recorded_and_can_be_removed(self):
        app, _ = self.make()
        client = self.finish_a_download(app)
        items = client.get("/api/history").get_json()
        self.assertEqual(len(items), 1)
        self.assertEqual((items[0]["title"], items[0]["format"], items[0]["kind"], items[0]["source"]), ("Late Night", "flac", "playlist", "soundcloud"))
        self.assertEqual(items[0]["url"], "https://soundcloud.com/a/sets/b")
        self.assertEqual(client.post("/api/history/delete", json={"id": items[0]["id"]}).get_json(), [])

    def test_history_can_be_turned_off_and_cleared(self):
        app, _ = self.make()
        app.test_client().post("/api/settings", json={"history": False})
        self.assertEqual(self.finish_a_download(app).get("/api/history").get_json(), [])
        app.test_client().post("/api/settings", json={"history": True})
        client = self.finish_a_download(app)
        self.assertEqual(len(client.get("/api/history").get_json()), 1)
        self.assertEqual(client.post("/api/history/clear", json={}).get_json(), [])

    def test_store_is_capped_newest_first_and_ignores_junk(self):
        import history, tempfile, json
        from pathlib import Path
        path = Path(tempfile.mkdtemp()) / "history.json"
        for number in range(history.MAX_ENTRIES + 5):
            history.add(path, {"id": f"id{number}", "title": f"Song {number}"})
        items = history.load(path)
        self.assertEqual(len(items), history.MAX_ENTRIES)
        self.assertEqual(items[0]["title"], f"Song {history.MAX_ENTRIES + 4}")
        path.write_text(json.dumps([{"id": 1}, "junk", {"id": "ok", "title": "Fine"}]))
        self.assertEqual([item["id"] for item in history.load(path)], ["ok"])


class StylesheetTests(unittest.TestCase):
    def test_key_rules_apply_in_every_theme(self):
        """A stray theme selector once scoped the waveform layout to light mode only, collapsing it in dark mode."""
        import re
        from pathlib import Path
        css = re.sub(r"/\*.*?\*/", "", (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text(), flags=re.S)
        selectors = [selector.strip() for selector in re.findall(r"(?:^|[};])\s*([^{};@]+)\{", css)]
        for required in (".wave-canvas", ".progress-bar", ".track-row", ".button-primary", ".history-dialog", ".history-item", ".history-load", ".chip", ".settings-dialog"):
            self.assertIn(required, selectors, f"{required} must be an unscoped rule")
        self.assertEqual(css.count("{"), css.count("}"))


class ArtworkTests(unittest.TestCase):
    def test_spotify_track_cover_comes_from_visual_identity(self):
        entity = {"visualIdentity": {"image": [{"url": "https://image-cdn-ak.spotifycdn.com/image/abc"}]}}
        self.assertEqual(spotify._safe_cover(entity), "https://image-cdn-ak.spotifycdn.com/image/abc")
        self.assertIsNone(spotify._safe_cover({"visualIdentity": {"image": [{"url": "https://evil.example/x.png"}]}}))

    def test_soundcloud_song_cover_is_taken_from_the_raw_record(self):
        import downloader
        raw = {"id": 1, "title": "Song", "artwork_url": "https://i1.sndcdn.com/artworks-abc-large.jpg", "user": {"username": "u"}}
        ie = downloader._TrackMetadataIE()
        with patch.object(downloader.SoundcloudIE, "_extract_info_dict", return_value={"id": "1", "title": "Song"}):
            result = ie._extract_info_dict(raw)
        self.assertEqual(result["cover"], "https://i1.sndcdn.com/artworks-abc-t500x500.jpg")
        self.assertEqual(downloader._safe_artwork(result["cover"]), result["cover"])

    def test_csp_allows_both_image_hosts(self):
        header = crate.create_app().test_client().get("/api/health").headers["Content-Security-Policy"]
        self.assertIn("*.sndcdn.com", header)
        self.assertIn("*.spotifycdn.com", header)


class YouTubeTests(unittest.TestCase):
    def test_links_are_classified_and_unsafe_ones_refused(self):
        from downloader import classify_youtube_url
        video = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
        for url in ("https://youtu.be/aqz-KE-bpKQ?si=x", video + "&list=PLabcdefghijklmn", "https://music.youtube.com/watch?v=aqz-KE-bpKQ",
                    "https://m.youtube.com/shorts/aqz-KE-bpKQ"):
            self.assertEqual(classify_youtube_url(url), ("track", video))
        self.assertEqual(classify_youtube_url("https://www.youtube.com/playlist?list=PLabcdefghijklmn&si=1"),
                         ("playlist", "https://www.youtube.com/playlist?list=PLabcdefghijklmn"))
        for bad in ("https://www.youtube.com/watch?v=short", "https://www.youtube.com/playlist?list=RDabcdefghijk",
                    "https://youtube.com.evil.example/watch?v=aqz-KE-bpKQ", "https://www.youtube.com/@someone", "https://youtu.be/"):
            with self.assertRaises(ValueError):
                classify_youtube_url(bad)

    def test_titles_are_split_into_artist_and_song(self):
        from downloader import _youtube_names
        self.assertEqual(_youtube_names("Daft Punk - One More Time (Official Video)", "Daft Punk"), ("Daft Punk", "One More Time"))
        self.assertEqual(_youtube_names("Slow Burner", "Interplanetary Criminal - Topic"), ("Interplanetary Criminal", "Slow Burner"))

    def test_endpoint_routes_youtube_links_and_allows_their_images(self):
        client = crate.create_app().test_client()
        preview = {"source": "youtube", "kind": "track", "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "title": "T", "creator": "C",
                   "artwork": None, "track_count": 1, "tracks": [{"id": "1", "title": "T", "artist": "C", "url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "available": True}]}
        with patch.object(crate, "preview_youtube", return_value=preview) as reader:
            response = client.post("/api/preview", json={"url": "https://youtu.be/aqz-KE-bpKQ"})
        self.assertEqual(response.status_code, 200)
        reader.assert_called_once_with("https://www.youtube.com/watch?v=aqz-KE-bpKQ")
        self.assertIn("*.ytimg.com", response.headers["Content-Security-Policy"])
        self.assertEqual(client.post("/api/preview", json={"url": "https://www.youtube.com/@someone"}).status_code, 400)


class YouTubeFallbackTests(unittest.TestCase):
    def search(self, entries, track):
        with patch.object(spotify.yt_dlp, "YoutubeDL") as ydl:
            ydl.return_value.__enter__.return_value.extract_info.return_value = {"entries": entries}
            return spotify.find_on_youtube(track)

    def test_same_recording_is_matched_and_variants_are_not(self):
        track = {"title": "Let You Go", "artist": "Jeremy Sylvester", "duration": 284.0}
        good = {"id": "aaaaaaaaaaa", "title": "Jeremy Sylvester - Let You Go", "channel": "Jeremy Sylvester - Topic", "duration": 285}
        remix = {"id": "bbbbbbbbbbb", "title": "Jeremy Sylvester - Let You Go (Remix)", "channel": "DJ X", "duration": 284}
        clip = {"id": "ccccccccccc", "title": "Jeremy Sylvester - Let You Go", "channel": "Jeremy Sylvester", "duration": 38}
        urls, note = self.search([remix, clip, good], track)
        self.assertEqual(urls, ["https://www.youtube.com/watch?v=aaaaaaaaaaa"])
        self.assertEqual(note, "Used a YouTube copy")
        with self.assertRaises(ValueError):
            self.search([remix, clip], track)

    def test_typed_song_search_picks_the_plain_version(self):
        import downloader
        entries = [
            {"id": "ddddddddddd", "title": "Let You Go (Slowed + Reverb)", "channel": "Chill", "duration": 250},
            {"id": "eeeeeeeeeee", "title": "Jeremy Sylvester - Let You Go (Official Audio)", "channel": "Jeremy Sylvester", "duration": 284},
            {"id": "fffffffffff", "title": "Let You Go 10 HOUR LOOP", "channel": "Loops", "duration": 36000},
        ]
        picked = {}
        with patch.object(downloader.yt_dlp, "YoutubeDL") as ydl, patch.object(downloader, "preview_youtube", side_effect=lambda url: picked.setdefault("url", url) and {"ok": True}):
            ydl.return_value.__enter__.return_value.extract_info.return_value = {"entries": entries}
            downloader.preview_search("let you go jeremy sylvester")
        self.assertEqual(picked["url"], "https://www.youtube.com/watch?v=eeeeeeeeeee")
        with patch.object(downloader.yt_dlp, "YoutubeDL") as ydl:
            ydl.return_value.__enter__.return_value.extract_info.return_value = {"entries": entries[:1]}
            with self.assertRaises(ValueError):
                downloader.preview_search("let you go jeremy sylvester")

    def test_search_text_is_told_apart_from_links(self):
        from downloader import looks_like_search
        self.assertTrue(looks_like_search("let you go jeremy sylvester"))
        for text in ("https://soundcloud.com/a/b", "soundcloud.com/a/b", "ab", "x" * 200):
            self.assertFalse(looks_like_search(text))


class RepeatSongTests(unittest.TestCase):
    def make(self):
        import tempfile
        from pathlib import Path
        folder = Path(tempfile.mkdtemp())
        return crate.create_app(folder / "dl", folder / "settings.json"), folder

    def test_songs_match_by_link_or_by_forgiving_title_and_artist(self):
        import history, tempfile
        from pathlib import Path
        path = Path(tempfile.mkdtemp()) / "history.json"
        history.add(path, {"id": "j1", "title": "Mix", "songs": [
            [history.song_key("Interplanetary Criminal", "Slow Burner"), "https://soundcloud.com/x/slow-burner"],
            [history.song_key("Ashe", "Stop The Wedding!"), ""],
        ]})
        found = history.find_known(path, [
            {"id": "a", "artist": "Interplanetary Criminal, Original Koffee", "title": "Slow Burner (feat. Original Koffee)", "url": ""},
            {"id": "b", "artist": "someone", "title": "different name", "url": "https://soundcloud.com/x/slow-burner"},
            {"id": "c", "artist": "Ashe", "title": "stop the wedding", "url": ""},
            {"id": "d", "artist": "Ashe", "title": "Moral of the Story", "url": ""},
        ])
        self.assertEqual(sorted(found), ["a", "b", "c"])

    def run_job(self, app, tracks, skip=None):
        import zipfile
        from pathlib import Path
        client = app.test_client()
        preview = {"url": "https://soundcloud.com/a/sets/b", "title": "Mix", "creator": "Nova", "artwork": None, "track_count": len(tracks), "tracks": tracks}
        seen = {}

        def fake_download(data, directory, fmt, update, event, **_):
            seen["titles"] = [t["title"] for t in data["tracks"]]
            update({"completed_tracks": len(data["tracks"]), "failed_tracks": 0, "total_tracks": len(data["tracks"]),
                    "tracks": [{**t, "status": "complete"} for t in data["tracks"]]})
            archive = Path(directory) / "Mix.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("a.mp3", b"x")
            return archive

        with patch.object(crate, "preview_playlist", return_value=preview), patch.object(crate, "download_playlist", fake_download):
            result = client.post("/api/preview", json={"url": "https://soundcloud.com/a/sets/b"}).get_json()
            response = client.post("/api/jobs", json={"preview_id": result["id"], "format": "mp3", **({"skip_ids": skip} if skip is not None else {})})
            if response.status_code == 202:
                app.extensions["jobs"].threads[response.get_json()["id"]].join(timeout=5)
        return client, result, response, seen

    TRACKS = [{"id": "1", "title": "First", "artist": "Nova", "url": "https://soundcloud.com/n/first", "available": True},
              {"id": "2", "title": "Second", "artist": "Nova", "url": "https://soundcloud.com/n/second", "available": True}]

    def test_finished_songs_are_remembered_and_flagged_on_the_next_preview(self):
        app, _ = self.make()
        client, first, _, _ = self.run_job(app, [dict(t) for t in self.TRACKS])
        self.assertFalse(any("previously" in t for t in first["tracks"]))            # nothing downloaded yet
        _, second, _, _ = self.run_job(app, [dict(t) for t in self.TRACKS] + [{"id": "3", "title": "Third", "artist": "Nova", "url": "", "available": True}])
        self.assertEqual([t["id"] for t in second["tracks"] if t.get("previously")], ["1", "2"])

    def test_skipped_songs_are_left_out_of_the_job(self):
        app, _ = self.make()
        _, _, _, seen = self.run_job(app, [dict(t) for t in self.TRACKS], skip=["1"])
        self.assertEqual(seen["titles"], ["Second"])
        _, _, response, _ = self.run_job(app, [dict(t) for t in self.TRACKS], skip=["1", "2"])
        self.assertEqual(response.status_code, 400)

    def test_setting_off_or_history_off_means_no_flags(self):
        for patch_body in ({"skip_downloaded": False}, {"history": False}):
            app, _ = self.make()
            self.run_job(app, [dict(t) for t in self.TRACKS])
            app.test_client().post("/api/settings", json=patch_body)
            _, result, _, _ = self.run_job(app, [dict(t) for t in self.TRACKS])
            self.assertFalse(any("previously" in t for t in result["tracks"]), patch_body)


class HistorySongListTests(RepeatSongTests):
    def test_history_keeps_every_song_with_its_outcome(self):
        app, _ = self.make()
        tracks = [dict(t) for t in self.TRACKS] + [{"id": "3", "title": "Third", "artist": "Nova", "url": "", "available": True}]
        client, _, response, _ = self.run_job(app, tracks, skip=["1"])           # the person unticked song 1
        job_id = response.get_json()["id"]
        listing = client.get("/api/history").get_json()
        self.assertEqual(len(listing), 1)
        self.assertNotIn("tracks", listing[0]); self.assertNotIn("songs", listing[0])   # the list stays light
        self.assertEqual(listing[0]["track_count"], 3)
        detail = client.get(f"/api/history/{job_id}").get_json()
        self.assertEqual([(t["title"], t["status"]) for t in detail["tracks"]],
                         [("First", "skipped"), ("Second", "done"), ("Third", "done")])
        self.assertEqual(client.get("/api/history/does-not-exist").status_code, 404)

    def test_old_entries_without_songs_still_load(self):
        import history, tempfile
        from pathlib import Path
        path = Path(tempfile.mkdtemp()) / "history.json"
        path.write_text('[{"id": "old", "title": "Old Mix", "kind": "playlist", "format": "mp3"}]')
        entry = history.load(path)[0]
        self.assertEqual((entry["tracks"], entry["songs"]), ([], []))
        self.assertEqual(history.summary(entry)["track_count"], 0)


class LegacyHistoryTests(unittest.TestCase):
    def test_single_songs_saved_before_song_tracking_still_count_as_downloaded(self):
        import history, tempfile
        from pathlib import Path
        path = Path(tempfile.mkdtemp()) / "history.json"
        # How an entry looked before songs were recorded: just the entry itself.
        path.write_text('[{"id":"old","title":"CHRIS STASSY & Interplanetary Criminal - Dangerous ep - uts29","creator":"Up The Stuss",'
                        '"kind":"track","format":"mp3","url":"https://soundcloud.com/upthestuss/cs-ipc-dangerous-uts29","at":"2026-11-10T18:00:00+00:00"}]')
        found = history.find_known(path, [
            {"id": "a", "artist": "Up The Stuss", "title": "CHRIS STASSY & Interplanetary Criminal - Dangerous ep - uts29", "url": ""},
            {"id": "b", "artist": "x", "title": "y", "url": "https://soundcloud.com/upthestuss/cs-ipc-dangerous-uts29"},
            {"id": "c", "artist": "Up The Stuss", "title": "Something else", "url": ""},
        ])
        self.assertEqual(sorted(found), ["a", "b"])
