"""HTTP and job lifecycle contracts, without SoundCloud network access."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

import app as crate


PLAYLIST_URL = "https://soundcloud.com/test-artist/sets/weekend-sessions"
PLAYLIST = {
    "url": PLAYLIST_URL,
    "title": "Weekend Sessions",
    "creator": "Test Artist",
    "tracks": [
        {"index": 1, "title": "First Song", "url": "https://soundcloud.com/test-artist/first-song"},
        {"index": 2, "title": "Unavailable Song", "url": "https://soundcloud.com/test-artist/unavailable-song"},
    ],
}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "jobs"
        with patch.object(crate, "get_capabilities", return_value={"mp3_available": True}):
            self.app = crate.create_app(self.directory)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.manager = self.app.extensions["jobs"]
        self.releases: list[threading.Event] = []

    def tearDown(self):
        # Always release test workers, including when an assertion has failed.
        for job_id in list(self.manager.jobs):
            self.manager.cancel(job_id)
        for release in self.releases:
            release.set()
        for worker in list(self.manager.threads.values()):
            worker.join(timeout=5)
        self.temporary.cleanup()

    def preview(self):
        with patch.object(crate, "preview_playlist", return_value=copy.deepcopy(PLAYLIST)) as extractor:
            response = self.client.post("/api/preview", json={"url": PLAYLIST_URL})
        self.assertEqual(response.status_code, 200)
        extractor.assert_called_once_with(PLAYLIST_URL)
        return response.get_json()

    def wait_for_worker(self, job_id):
        worker = self.manager.threads[job_id]
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "Download worker did not finish")
        return self.client.get(f"/api/jobs/{job_id}").get_json()

    @staticmethod
    def write_archive(directory):
        archive = directory / "Weekend Sessions.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("01 - First Song.mp3", b"fixture audio")
            bundle.writestr("download-report.txt", "Unavailable Song: unavailable\n")
        return archive

    def test_invalid_playlist_urls_fail_before_extraction(self):
        invalid_urls = [
            "https://example.com/artist/sets/playlist",
            "http://127.0.0.1/artist/sets/playlist",
            "https://soundcloud.com.evil.example/artist/sets/playlist",
            "file:///etc/passwd",
            "",
            None,
            [],
            {},
        ]
        with patch.object(crate, "preview_playlist") as extractor:
            for url in invalid_urls:
                with self.subTest(url=url):
                    response = self.client.post("/api/preview", json={"url": url})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())
            extractor.assert_not_called()

    def test_untrusted_host_and_cross_origin_requests_are_rejected(self):
        with patch.object(crate, "preview_playlist") as extractor:
            response = self.client.post(
                "/api/preview", json={"url": PLAYLIST_URL}, headers={"Host": "attacker.example"}
            )
            self.assertEqual(response.status_code, 403)
            response = self.client.post(
                "/api/preview", json={"url": PLAYLIST_URL}, headers={"Origin": "https://attacker.example"}
            )
            self.assertEqual(response.status_code, 403)
            extractor.assert_not_called()

    def test_same_origin_json_request_and_security_headers(self):
        with patch.object(crate, "preview_playlist", return_value=copy.deepcopy(PLAYLIST)):
            response = self.client.post(
                "/api/preview", json={"url": PLAYLIST_URL}, headers={"Origin": "http://localhost"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_mutations_require_json_and_handle_malformed_json(self):
        with patch.object(crate, "preview_playlist") as extractor:
            response = self.client.post("/api/preview", data={"url": PLAYLIST_URL})
            self.assertEqual(response.status_code, 415)
            response = self.client.post("/api/preview", data="{", content_type="application/json")
            self.assertEqual(response.status_code, 400)
            self.assertIn("error", response.get_json())
            for body in ([], "a playlist", 42, None):
                with self.subTest(body=body):
                    response = self.client.post("/api/preview", data=json.dumps(body), content_type="application/json")
                    self.assertEqual(response.status_code, 400)
            extractor.assert_not_called()

    def test_preview_failure_has_actionable_json_error(self):
        with patch.object(crate, "preview_playlist", side_effect=ValueError("This playlist has no tracks.")):
            response = self.client.post("/api/preview", json={"url": PLAYLIST_URL})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "This playlist has no tracks.")

    def test_upstream_preview_failure_is_sanitized_and_releases_preview_lock(self):
        with patch.object(crate, "preview_playlist", side_effect=RuntimeError("private upstream details")), self.assertLogs(crate.logger, level="ERROR"):
            response = self.client.post("/api/preview", json={"url": PLAYLIST_URL})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private upstream details", response.get_json()["error"])
        self.assertIn("Couldn't load", response.get_json()["error"])
        self.assertIn("id", self.preview())

    def test_only_one_preview_is_extracted_at_a_time(self):
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)
        results = []

        def extract(url):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Test did not release preview worker")
            return copy.deepcopy(PLAYLIST)

        def first_request():
            with self.app.test_client() as client:
                results.append(client.post("/api/preview", json={"url": PLAYLIST_URL}))

        with patch.object(crate, "preview_playlist", side_effect=extract) as extractor:
            worker = threading.Thread(target=first_request, daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(timeout=3))
                response = self.client.post("/api/preview", json={"url": PLAYLIST_URL})
                self.assertEqual(response.status_code, 409)
            finally:
                release.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(results[0].status_code, 200)
            self.assertEqual(extractor.call_count, 1)

    def test_partial_success_produces_downloadable_zip_and_track_results(self):
        preview = self.preview()

        def download(preview_data, directory, audio_format, update, cancellation, **_):
            self.assertEqual(audio_format, "mp3")
            self.assertFalse(cancellation.is_set())
            update({
                "status": "downloading",
                "completed_tracks": 1,
                "failed_tracks": 1,
                "tracks": [
                    {**preview_data["tracks"][0], "status": "completed"},
                    {**preview_data["tracks"][1], "status": "failed", "error": "Unavailable"},
                ],
            })
            return self.write_archive(directory)

        with patch.object(crate, "download_playlist", side_effect=download) as downloader:
            response = self.client.post("/api/jobs", json={"preview_id": preview["id"], "format": "mp3"})
            self.assertEqual(response.status_code, 202)
            job_id = response.get_json()["id"]
            job = self.wait_for_worker(job_id)
        downloader.assert_called_once()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["progress"], 100)
        self.assertEqual(job["completed_tracks"], 1)
        self.assertEqual(job["failed_tracks"], 1)
        self.assertEqual([track["status"] for track in job["tracks"]], ["completed", "failed"])
        self.assertNotIn("_archive", job)
        archive_response = self.client.get(job["download_url"])
        self.assertEqual(archive_response.status_code, 200)
        self.assertEqual(archive_response.mimetype, "application/zip")
        self.assertIn("attachment", archive_response.headers["Content-Disposition"])
        self.assertEqual(len(archive_response.data), job["archive_size"])
        with zipfile.ZipFile(io.BytesIO(archive_response.data)) as bundle:
            self.assertIsNone(bundle.testzip())
            self.assertEqual(bundle.read("01 - First Song.mp3"), b"fixture audio")
            self.assertIn("Unavailable Song", bundle.read("download-report.txt").decode())
        archive_response.close()

    def test_second_download_is_rejected_until_first_finishes(self):
        preview = self.preview()
        entered = threading.Event()
        release = threading.Event()
        self.releases.append(release)

        def download(preview_data, directory, audio_format, update, cancellation, **_):
            update({"status": "downloading"})
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("Test did not release download worker")
            return self.write_archive(directory)

        with patch.object(crate, "download_playlist", side_effect=download) as downloader:
            first = self.client.post("/api/jobs", json={"preview_id": preview["id"], "format": "original"})
            self.assertEqual(first.status_code, 202)
            job_id = first.get_json()["id"]
            self.assertTrue(entered.wait(timeout=3))
            second = self.client.post("/api/jobs", json={"preview_id": preview["id"], "format": "original"})
            self.assertEqual(second.status_code, 409)
            self.assertEqual(self.client.get(f"/api/jobs/{job_id}/download").status_code, 409)
            release.set()
            self.assertEqual(self.wait_for_worker(job_id)["status"], "completed")
            downloader.assert_called_once()

    def test_cancellation_stops_worker_and_removes_partial_files(self):
        preview = self.preview()
        entered = threading.Event()

        def download(preview_data, directory, audio_format, update, cancellation, **_):
            (directory / "unfinished.part").write_bytes(b"partial audio")
            update({"status": "downloading"})
            entered.set()
            if not cancellation.wait(timeout=5):
                raise RuntimeError("Test did not request cancellation")
            raise crate.DownloadCancelled()

        with patch.object(crate, "download_playlist", side_effect=download):
            response = self.client.post("/api/jobs", json={"preview_id": preview["id"], "format": "original"})
            self.assertEqual(response.status_code, 202)
            job_id = response.get_json()["id"]
            self.assertTrue(entered.wait(timeout=3))
            cancelled = self.client.post(f"/api/jobs/{job_id}/cancel", json={})
            self.assertEqual(cancelled.status_code, 200)
            self.assertTrue(cancelled.get_json()["cancel_requested"])
            job = self.wait_for_worker(job_id)
        self.assertEqual(job["status"], "cancelled")
        self.assertFalse((self.directory / job_id).exists())
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}/download").status_code, 409)

    def test_worker_failure_removes_partial_files(self):
        preview = self.preview()

        def download(preview_data, directory, audio_format, update, cancellation, **_):
            (directory / "unfinished.part").write_bytes(b"partial audio")
            raise RuntimeError("No tracks could be downloaded.")

        with patch.object(crate, "download_playlist", side_effect=download), self.assertLogs(crate.logger, level="ERROR"):
            response = self.client.post("/api/jobs", json={"preview_id": preview["id"], "format": "original"})
            job_id = response.get_json()["id"]
            job = self.wait_for_worker(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertIn("No tracks", job["message"])
        self.assertFalse((self.directory / job_id).exists())

    def test_cancellation_while_finalizing_archive_prevents_publication(self):
        preview = self.preview()
        entered_validation = threading.Event()
        release_validation = threading.Event()
        self.releases.append(release_validation)
        original_is_file = Path.is_file

        def download(preview_data, directory, audio_format, update, cancellation, **_):
            return self.write_archive(directory)

        def gated_is_file(path):
            if path.name == "Weekend Sessions.zip":
                entered_validation.set()
                if not release_validation.wait(timeout=5):
                    raise RuntimeError("Test did not release archive validation")
            return original_is_file(path)

        with patch.object(crate, "download_playlist", side_effect=download), patch.object(Path, "is_file", gated_is_file):
            response = self.client.post("/api/jobs", json={"preview_id": preview["id"], "format": "original"})
            self.assertEqual(response.status_code, 202)
            job_id = response.get_json()["id"]
            self.assertTrue(entered_validation.wait(timeout=3))
            cancelled = self.client.post(f"/api/jobs/{job_id}/cancel", json={})
            self.assertEqual(cancelled.status_code, 200)
            release_validation.set()
            job = self.wait_for_worker(job_id)
        self.assertEqual(job["status"], "cancelled")
        self.assertNotIn("download_url", job)
        self.assertFalse((self.directory / job_id).exists())

    def test_archive_outside_job_directory_is_never_published(self):
        preview = self.preview()
        outside = Path(self.temporary.name) / "private.zip"
        outside.write_bytes(b"private bytes")
        with patch.object(crate, "download_playlist", return_value=outside), self.assertLogs(crate.logger, level="ERROR"):
            response = self.client.post("/api/jobs", json={"preview_id": preview["id"], "format": "original"})
            job_id = response.get_json()["id"]
            job = self.wait_for_worker(job_id)
        self.assertEqual(job["status"], "failed")
        self.assertFalse((self.directory / job_id).exists())
        self.assertEqual(outside.read_bytes(), b"private bytes")
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}/download").status_code, 409)

    def test_missing_completed_archive_returns_expired_file_error(self):
        preview = self.preview()

        def download(preview_data, directory, audio_format, update, cancellation, **_):
            return self.write_archive(directory)

        with patch.object(crate, "download_playlist", side_effect=download):
            response = self.client.post("/api/jobs", json={"preview_id": preview["id"], "format": "original"})
            job_id = response.get_json()["id"]
            self.assertEqual(self.wait_for_worker(job_id)["status"], "completed")
        Path(self.manager.jobs[job_id]["_archive"]).unlink()
        response = self.client.get(f"/api/jobs/{job_id}/download")
        self.assertEqual(response.status_code, 410)
        self.assertIn("error", response.get_json())

    def test_unknown_jobs_and_expired_previews(self):
        self.assertEqual(self.client.get("/api/jobs/unknown").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/unknown/download").status_code, 404)
        self.assertEqual(self.client.post("/api/jobs/unknown/cancel", json={}).status_code, 404)
        preview = self.preview()
        self.manager.previews[preview["id"]]["created"] = time.time() - crate.PREVIEW_SECONDS - 1
        with patch.object(crate, "download_playlist") as downloader:
            response = self.client.post("/api/jobs", json={"preview_id": preview["id"], "format": "original"})
            self.assertEqual(response.status_code, 400)
            self.assertIn("expired", response.get_json()["error"])
            downloader.assert_not_called()

    def test_malformed_job_bodies_and_formats_are_client_errors(self):
        preview = self.preview()
        invalid_bodies = [[], {}, {"preview_id": []}, {"preview_id": 42}]
        invalid_bodies.extend({"preview_id": preview["id"], "format": value} for value in ([], {}, None, 42, "midi"))
        with patch.object(crate, "download_playlist") as downloader:
            for body in invalid_bodies:
                with self.subTest(body=body):
                    response = self.client.post("/api/jobs", json=body)
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("error", response.get_json())
            downloader.assert_not_called()

    def test_mp3_requires_conversion_capability(self):
        with patch.object(crate, "get_capabilities", return_value={"mp3_available": False}):
            app = crate.create_app(self.directory)
        client = app.test_client()
        preview = app.extensions["jobs"].remember_preview(copy.deepcopy(PLAYLIST))
        with patch.object(crate, "download_playlist") as downloader:
            response = client.post("/api/jobs", json={"preview_id": preview["id"], "format": "mp3"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Original", response.get_json()["error"])
        downloader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
