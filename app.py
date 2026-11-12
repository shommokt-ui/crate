"""Crate: a loopback-only SoundCloud playlist-to-ZIP application."""

from __future__ import annotations

import argparse
import copy
import html
import json
import logging
import gzip
import hmac
import ipaddress
import mimetypes
import os
from pathlib import Path
import secrets
import shutil
import threading
import time
from urllib.parse import urlsplit
import uuid
import webbrowser

from flask import Flask, g, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException

from downloader import (
    AUDIO_FORMATS,
    DownloadCancelled,
    classify_soundcloud_url,
    classify_youtube_url,
    looks_like_search,
    preview_search,
    preview_youtube,
    download_playlist,
    get_capabilities,
    preview_playlist,
    preview_track,
)

import history as download_history
import phone
import phone_page
import settings as user_settings
from spotify import preview_spotify, validate_spotify_link

ROOT = Path(__file__).resolve().parent
RETENTION_SECONDS = 24 * 60 * 60
PREVIEW_SECONDS = 60 * 60
TERMINAL_STATES = {"completed", "failed", "cancelled"}
logger = logging.getLogger(__name__)


class JobManager:
    """Keep jobs in memory, with isolated files and a single download worker."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.RLock()
        self.previews: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.events: dict[str, threading.Event] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.settings_file = directory / "settings.json"
        self.history_file = directory / "history.json"
        self.cleanup()

    def cleanup(self):
        now = time.time()
        with self.lock:
            for key, entry in list(self.previews.items()):
                if now - entry["created"] > PREVIEW_SECONDS:
                    self.previews.pop(key)
            for key, job in list(self.jobs.items()):
                if job["status"] in TERMINAL_STATES and now - job["updated"] > RETENTION_SECONDS:
                    self.jobs.pop(key)
                    self.events.pop(key, None)
                    self.threads.pop(key, None)
                    shutil.rmtree(self.directory / key, ignore_errors=True)
            # Remove only our UUID-named job directories, never arbitrary files.
            if self.directory.is_dir():
                for child in self.directory.iterdir():
                    try:
                        is_job = str(uuid.UUID(child.name)) == child.name
                    except ValueError:
                        continue
                    if is_job and child.is_dir() and not child.is_symlink() and child.name not in self.jobs:
                        if now - child.stat().st_mtime > RETENTION_SECONDS:
                            shutil.rmtree(child, ignore_errors=True)

    def remember_preview(self, preview: dict) -> dict:
        with self.lock:
            if len(self.previews) >= 20:
                self.previews.pop(next(iter(self.previews)))
            result = {**copy.deepcopy(preview), "id": str(uuid.uuid4())}
            self.previews[result["id"]] = {"created": time.time(), "data": result}
            return result

    def snapshot(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return copy.deepcopy({key: value for key, value in job.items() if not key.startswith("_")})

    def start(self, preview_id: str, audio_format: str, skip_ids: frozenset = frozenset(), remote: bool = False) -> dict:
        with self.lock:
            entry = self.previews.get(preview_id)
            if not entry or time.time() - entry["created"] > PREVIEW_SECONDS:
                raise ValueError("This playlist preview has expired. Load the playlist again.")
            if any(job["status"] not in TERMINAL_STATES for job in self.jobs.values()):
                raise RuntimeError("A playlist is already downloading. Finish or cancel it before starting another.")
            preview = copy.deepcopy(entry["data"])
            full_list = copy.deepcopy(preview["tracks"])  # kept whole, so the history can show unticked songs too
            if skip_ids:
                preview["tracks"] = [track for track in preview["tracks"] if str(track.get("id")) not in skip_ids]
                if not preview["tracks"]:
                    raise ValueError("Everything here is already in your history.")
            job_id = str(uuid.uuid4())
            self.jobs[job_id] = {
                "id": job_id,
                "preview_id": preview_id,
                "url": preview["url"],
                "source": preview.get("source", "soundcloud"),
                "kind": preview.get("kind", "playlist"),
                "artwork": preview.get("artwork"),
                "title": preview["title"],
                "creator": preview.get("creator", ""),
                "format": audio_format,
                "status": "queued",
                "message": "Getting your playlist ready…",
                "progress": 0,
                "current_track": 0,
                "total_tracks": len(preview["tracks"]),
                "completed_tracks": 0,
                "failed_tracks": 0,
                "tracks": [{**track, "status": "pending"} for track in preview["tracks"]],
                "created": time.time(),
                "updated": time.time(),
                "_full_list": full_list,
                "_remote": remote,
            }
            self.events[job_id] = threading.Event()
            worker = threading.Thread(
                target=self._run, args=(job_id, preview, audio_format), daemon=True,
                name=f"crate-{job_id[:8]}",
            )
            self.threads[job_id] = worker
            worker.start()
            return self.snapshot(job_id)

    def _run(self, job_id: str, preview: dict, audio_format: str):
        directory = self.directory / job_id
        event = self.events[job_id]

        def update(values: dict):
            with self.lock:
                self.jobs[job_id].update(values)
                self.jobs[job_id]["updated"] = time.time()

        try:
            directory.mkdir(parents=True, exist_ok=False)
            if event.is_set():
                raise DownloadCancelled()
            prefs = user_settings.load(self.settings_file)
            if self.jobs[job_id].get("_remote"):
                prefs.pop("folder", None)  # a phone gets the files itself; nothing is saved into the Mac's folder
            archive = download_playlist(preview, directory, audio_format, update, event, options=prefs)
            if event.is_set():
                raise DownloadCancelled()
            archive = Path(archive).resolve()
            if not archive.is_file() or not archive.is_relative_to(directory.resolve()):
                raise RuntimeError("The ZIP archive could not be created.")
            archive_size = archive.stat().st_size
            with self.lock:
                # Cancellation and publication must be atomic with respect to
                # one another, including cancellation during archive checks.
                if event.is_set():
                    raise DownloadCancelled()
                done = self.jobs[job_id]
                note = done.get("delivered") or done.get("delivery_failed")
                update({
                    "status": "completed", "progress": 100,
                    "message": note or "Your playlist is packed and ready.",
                    "download_url": f"/api/jobs/{job_id}/download",
                    "archive_size": archive_size, "_archive": str(archive),
                })
                finished = dict(self.jobs[job_id])
            if user_settings.load(self.settings_file).get("history", True):
                links = {str(t.get("id")): t.get("url", "") for t in preview["tracks"]}
                outcomes = {str(row.get("id")): ("done" if row.get("status") in {"complete", "completed"} else "failed")
                            for row in finished["tracks"]}
                try:
                    download_history.add(self.history_file, {
                        "id": job_id, "title": preview.get("title"), "creator": preview.get("creator"),
                        "kind": preview.get("kind", "playlist"), "source": preview.get("source", "soundcloud"),
                        "format": audio_format, "total": finished["total_tracks"], "completed": finished["completed_tracks"],
                        "failed": finished["failed_tracks"], "url": preview.get("url"), "artwork": preview.get("artwork"),
                        "tracks": [
                            {"title": t.get("title", ""), "artist": t.get("artist", ""), "status": outcomes.get(str(t.get("id")), "skipped")}
                            for t in finished["_full_list"]
                        ],
                        "songs": [
                            [download_history.song_key(row.get("artist", ""), row.get("title", "")), links.get(str(row.get("id")), "")]
                            for row in finished["tracks"] if row.get("status") in {"complete", "completed"}
                        ],
                    })
                except OSError:
                    logger.warning("Could not save download history", exc_info=True)
        except DownloadCancelled:
            update({"status": "cancelled", "message": "Download cancelled."})
            shutil.rmtree(directory, ignore_errors=True)
        except Exception as exc:
            logger.exception("Playlist download failed")
            update({"status": "failed", "message": str(exc)[:600] or "The download failed. Please try again."})
            shutil.rmtree(directory, ignore_errors=True)

    def cancel(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            if job["status"] not in TERMINAL_STATES:
                self.events[job_id].set()
                job["message"] = "Cancelling… this may take a moment if a track is converting."
                job["cancel_requested"] = True
            return self.snapshot(job_id)


def create_app(data_dir: Path | None = None, settings_path: Path | None = None) -> Flask:
    app = Flask(__name__, static_folder=str(ROOT / "static"), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    manager = JobManager(data_dir or ROOT / ".runtime" / "downloads")
    settings_file = Path(settings_path) if settings_path else manager.directory / "settings.json"
    manager.settings_file = settings_file
    manager.history_file = settings_file.with_name("history.json")
    app.extensions["jobs"] = manager
    app.extensions["last_request"] = time.time()
    preview_lock = threading.Lock()
    capabilities = get_capabilities()

    def error(message, status=400):
        return jsonify(error=message), status

    token_file = settings_file.with_name("phone-token")

    def refuse_phone(message):
        """A phone opening the page gets something readable; API calls keep the JSON error."""
        if request.method != "GET" or request.path.startswith(("/api/", "/static/")):
            return error(message, 403)
        page = (
            '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            '<meta name="color-scheme" content="dark light"><title>Crate</title>'
            '<body style="margin:0;min-height:100vh;display:grid;place-items:center;font:16px/1.5 -apple-system,system-ui,sans-serif">'
            '<main style="max-width:320px;padding:32px;text-align:center"><img src="/static/logo.svg" alt="" width="56" height="56">'
            f'<h1 style="font-size:22px;margin:18px 0 8px">Can’t connect yet</h1><p style="margin:0;opacity:.7">{html.escape(message)}</p></main></body></html>'
        )
        return app.response_class(page, status=403, mimetype="text/html")

    def is_loopback(address: str | None) -> bool:
        try:
            return ipaddress.ip_address((address or "").split("%")[0]).is_loopback
        except ValueError:
            return False

    @app.before_request
    def local_requests_only():
        # Validate Host as well as the address the request came from, to prevent DNS rebinding.
        try:
            hostname = urlsplit(f"http://{request.host}").hostname or ""
        except ValueError:
            return error("Invalid host.")
        g.remote = not is_loopback(request.remote_addr)
        g.set_cookie = None
        if g.remote:
            # A phone on the same wifi: only while switched on, only on a local address, only with the pairing code.
            if not phone.host_is_local_network(hostname):
                return error("Crate only accepts requests on your own network.", 403)
            if not user_settings.load(settings_file).get("phone"):
                return refuse_phone("Phone access is off. Turn it on in Crate's settings on your Mac.")
            token = phone.load_token(token_file)
            query_matches = hmac.compare_digest(request.args.get("t", "").encode(), token.encode())
            cookie_matches = hmac.compare_digest(request.cookies.get("crate_phone", "").encode(), token.encode())
            # The page's own icon and manifest are fetched without the cookie and hold nothing private.
            public = request.path in {"/static/apple-touch-icon.png", "/static/manifest.webmanifest", "/static/logo.svg"}
            if not public and not (query_matches or cookie_matches):
                return refuse_phone("Open Crate's settings on your Mac and scan the code under Phone to connect.")
            # A freshly scanned code replaces an expired cookie. Public assets
            # may load without authentication, but must never grant access.
            if query_matches:
                g.set_cookie = token
        elif hostname not in {"localhost", "127.0.0.1", "::1"}:
            return error("Crate only accepts requests on this computer.", 403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                return error("Please use the Crate app.", 403)
            if not request.is_json:
                return error("Send this request as JSON.", 415)
        app.extensions["last_request"] = time.time()
        manager.cleanup()

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        nonce = getattr(g, "script_nonce", None)
        script_policy = f"'self' 'nonce-{nonce}'" if nonce else "'self'"
        response.headers["Content-Security-Policy"] = (
            f"default-src 'self'; script-src {script_policy}; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://*.sndcdn.com https://*.scdn.co https://*.spotifycdn.com https://*.ytimg.com https://*.ggpht.com; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if getattr(g, "set_cookie", None):
            response.set_cookie("crate_phone", g.set_cookie, max_age=365 * 24 * 3600, httponly=True, samesite="Lax")
        return response

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return error(exc.description, exc.code)

    @app.get("/")
    def index():
        # Settings ride along in a meta tag so the theme applies before first paint.
        page = (Path(app.static_folder) / "index.html").read_text(encoding="utf-8")
        payload = html.escape(json.dumps(user_settings.load(settings_file)), quote=True)
        page = page.replace("__SETTINGS__", payload)
        if g.remote:
            g.script_nonce = secrets.token_urlsafe(24)
            page = phone_page.render(page, Path(app.static_folder), g.script_nonce)
        response = app.response_class(page, mimetype="text/html")
        if g.remote and "gzip" in request.headers.get("Accept-Encoding", ""):
            # The phone's page is one large document; sent small, it survives slow or picky wifi.
            response.set_data(gzip.compress(response.get_data(), 6))
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Vary"] = "Accept-Encoding"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/settings")
    def get_settings():
        return jsonify(user_settings.load(settings_file))

    @app.get("/api/history")
    def get_history():
        return jsonify([download_history.summary(item) for item in download_history.load(manager.history_file)])

    @app.get("/api/history/<entry_id>")
    def get_history_item(entry_id):
        item = download_history.get(manager.history_file, entry_id)
        if not item:
            return error("That history item no longer exists.", 404)
        return jsonify({"id": item["id"], "title": item["title"], "tracks": item["tracks"]})

    @app.post("/api/history/delete")
    def delete_history_item():
        body = request.get_json()
        if not isinstance(body, dict) or not isinstance(body.get("id"), str):
            return error("Choose an item to remove.")
        download_history.remove(manager.history_file, body["id"])
        return jsonify([download_history.summary(item) for item in download_history.load(manager.history_file)])

    @app.post("/api/history/clear")
    def clear_history():
        download_history.clear(manager.history_file)
        return jsonify([])

    @app.post("/api/settings")
    def save_settings():
        body = request.get_json()
        if not isinstance(body, dict):
            return error("Invalid settings.")
        if g.remote:
            body = {key: value for key, value in body.items() if key not in {"phone", "folder"}}
        return jsonify(user_settings.save(settings_file, {**user_settings.load(settings_file), **body}))

    @app.get("/api/phone")
    def phone_info():
        if g.remote:
            return error("Open this on your Mac.", 403)
        ip = phone.lan_ip()
        enabled = bool(user_settings.load(settings_file).get("phone"))
        if not (enabled and ip):
            return jsonify(enabled=enabled, url=None, svg=None, reason=None if ip else "Connect your Mac to wifi first.")
        url = f"http://{ip}:{app.extensions.get('lan_port', 8765)}/?t={phone.load_token(token_file)}"
        return jsonify(enabled=True, url=url, svg=phone.qr_svg(url))

    @app.post("/api/phone/reset")
    def phone_reset():
        if g.remote:
            return error("Open this on your Mac.", 403)
        phone.load_token(token_file, reset=True)
        return jsonify(ok=True)

    @app.get("/api/health")
    def health():
        return jsonify(ok=True, capabilities=capabilities)

    @app.post("/api/preview")
    def preview():
        body = request.get_json()
        if not isinstance(body, dict):
            return error("Paste a link.")
        raw_input = body.get("url").strip() if isinstance(body.get("url"), str) else ""
        searching = looks_like_search(raw_input)
        host = urlsplit(raw_input).hostname
        spotify_source = host == "open.spotify.com"
        youtube_source = host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
        try:
            if searching:
                url = raw_input
            elif spotify_source:
                url = validate_spotify_link(body.get("url", ""))[1]
            elif youtube_source:
                url = classify_youtube_url(body.get("url", ""))[1]
            else:
                kind, url = classify_soundcloud_url(body.get("url", ""))
        except (ValueError, TypeError) as exc:
            return error(str(exc))
        if not preview_lock.acquire(blocking=False):
            return error("A playlist is loading. Please wait a moment and try again.", 409)
        try:
            if searching:
                result = preview_search(url)
            elif spotify_source:
                result = preview_spotify(url)
            elif youtube_source:
                result = preview_youtube(url)
            else:
                result = preview_playlist(url) if kind == "playlist" else preview_track(url)
            current = user_settings.load(settings_file)
            if current.get("history") and current.get("skip_downloaded"):
                for track_id, seen in download_history.find_known(manager.history_file, result["tracks"]).items():
                    for track in result["tracks"]:
                        if str(track.get("id")) == track_id:
                            track["previously"] = seen["at"]
            return jsonify(manager.remember_preview(result))
        except ValueError as exc:
            return error(str(exc))
        except Exception:
            logger.exception("Could not load playlist")
            return error("Couldn't load that link. Check that it's public and try again.", 502)
        finally:
            preview_lock.release()

    @app.post("/api/jobs")
    def start_job():
        body = request.get_json()
        if not isinstance(body, dict) or not isinstance(body.get("preview_id"), str):
            return error("Load a playlist before downloading.")
        audio_format = body.get("format", "mp3")
        if not isinstance(audio_format, str) or audio_format not in AUDIO_FORMATS:
            return error("Choose a supported format.")
        if audio_format != "original" and not capabilities.get("mp3_available"):
            return error("Audio conversion is unavailable. Choose Original, or reinstall the app dependencies.")
        try:
            skip = body.get("skip_ids")
            skip_ids = frozenset(str(item) for item in skip) if isinstance(skip, list) and all(isinstance(item, (str, int)) for item in skip) else frozenset()
            return jsonify(manager.start(body["preview_id"], audio_format, skip_ids, remote=g.remote)), 202
        except ValueError as exc:
            return error(str(exc))
        except RuntimeError as exc:
            return error(str(exc), 409)

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id):
        result = manager.snapshot(job_id)
        if not result:
            return error("This download has expired or the app was restarted. Load the playlist again.", 404)
        return jsonify(result)

    @app.post("/api/jobs/<job_id>/cancel")
    def cancel_job(job_id):
        result = manager.cancel(job_id)
        if not result:
            return error("This download could not be found.", 404)
        return jsonify(result)

    @app.get("/api/jobs/<job_id>/download")
    def download(job_id):
        with manager.lock:
            job = manager.jobs.get(job_id)
            if not job:
                return error("This download has expired or the app was restarted.", 404)
            if job["status"] != "completed":
                return error("The ZIP is not ready yet.", 409)
            archive = Path(job["_archive"])
            if not archive.is_file():
                return error("The ZIP is no longer available. Load the playlist again.", 410)
            job["updated"] = time.time()
        return send_file(archive, as_attachment=True, download_name=archive.name,
                         mimetype=mimetypes.guess_type(archive.name)[0] or "application/octet-stream")

    return app


def quit_when_idle(application: Flask, idle_seconds: int):
    """Exit once nothing has used the app for a while, so a Finder launch never lingers."""
    manager = application.extensions["jobs"]
    while True:
        time.sleep(30)
        with manager.lock:
            busy = any(job["status"] not in TERMINAL_STATES for job in manager.jobs.values())
        if not busy and time.time() - application.extensions["last_request"] > idle_seconds:
            logger.info("Idle for %d minutes; quitting.", idle_seconds // 60)
            os._exit(0)


def main():
    parser = argparse.ArgumentParser(description="Crate — download a SoundCloud playlist as one ZIP")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--no-browser", action="store_true", help="Don't open a browser automatically")
    parser.add_argument("--idle-quit", type=int, default=0, metavar="MINUTES", help="Quit after this many idle minutes (0 = never)")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    application = create_app()
    url = f"http://127.0.0.1:{args.port}"
    print(f"\nCrate is starting at {url}\nKeep this window open while downloading. Press Ctrl+C to stop.\n", flush=True)
    if not args.no_browser:
        opener = threading.Timer(1.0, webbrowser.open, args=(url,))
        opener.daemon = True
        opener.start()
    if args.idle_quit > 0:
        threading.Thread(target=quit_when_idle, args=(application, args.idle_quit * 60), daemon=True).start()
    application.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
