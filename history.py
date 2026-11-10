"""Download history: a small JSON list of what was downloaded (metadata only, never audio)."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

MAX_ENTRIES = 200
_lock = threading.Lock()
_FIELDS = ("id", "title", "creator", "kind", "source", "format", "total", "completed", "failed", "url", "artwork", "at", "songs", "tracks")
MAX_SONGS_PER_ENTRY = 1000
MAX_TRACKS_SHOWN = 500
_STATUSES = {"done", "failed", "skipped"}


def _clean(entry: object) -> dict | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("title"), str):
        return None
    clean = {key: entry.get(key) for key in _FIELDS}
    for key in ("title", "creator", "kind", "source", "format", "url", "artwork", "at"):
        value = clean[key]
        clean[key] = value[:500] if isinstance(value, str) else ""
    for key in ("total", "completed", "failed"):
        clean[key] = clean[key] if isinstance(clean[key], int) and clean[key] >= 0 else 0
    songs = entry.get("songs")
    clean["songs"] = [
        [str(key)[:200], str(url)[:500]] for key, url in (item for item in songs if isinstance(item, (list, tuple)) and len(item) == 2)
    ][:MAX_SONGS_PER_ENTRY] if isinstance(songs, list) else []
    tracks = entry.get("tracks")
    clean["tracks"] = [
        {"title": str(item.get("title", ""))[:300], "artist": str(item.get("artist", ""))[:200],
         "status": item.get("status") if item.get("status") in _STATUSES else "done"}
        for item in tracks if isinstance(item, dict)
    ][:MAX_TRACKS_SHOWN] if isinstance(tracks, list) else []
    return clean


def _words(value: str) -> list[str]:
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"[\(\[](?:feat|ft|with|prod)\b[^\)\]]*[\)\]]", " ", value)  # "(feat. X)" doesn't make a different song
    return re.findall(r"[a-z0-9]+", value)


def song_key(artist: str, title: str) -> str:
    """A forgiving identity for a song: the first word of the artist plus the cleaned title."""
    names = _words(artist)
    return f"{names[0] if names else ''}|{' '.join(_words(title))}"


def find_known(path: Path, tracks: list[dict]) -> dict[str, dict]:
    """Map track id -> {"at", "title"} for tracks whose song (or link) is already in the history."""
    by_key, by_url = {}, {}
    for entry in load(path):  # newest first, so the first sighting is the most recent download
        info = {"at": entry["at"], "title": entry["title"]}
        songs = list(entry["songs"])
        if entry["kind"] == "track" and not songs:  # saved before songs were tracked: the entry itself is the song
            songs = [[song_key(entry["creator"], entry["title"]), entry["url"]]]
        for key, url in songs:
            by_key.setdefault(key, info)
            if url:
                by_url.setdefault(url, info)
    found = {}
    for track in tracks:
        match = by_url.get(track.get("url") or "") or by_key.get(song_key(track.get("artist", ""), track.get("title", "")))
        if match and track.get("id"):
            found[str(track["id"])] = match
    return found


def load(path: Path) -> list[dict]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return []
    return [entry for entry in map(_clean, data if isinstance(data, list) else []) if entry]


def _write(path: Path, entries: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".history-")
    try:
        with os.fdopen(handle, "w") as file:
            json.dump(entries, file, indent=1)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def add(path: Path, entry: dict) -> None:
    """Record one finished download, newest first, replacing any earlier entry with the same id."""
    clean = _clean({**entry, "at": entry.get("at") or datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if not clean:
        return
    with _lock:
        entries = [item for item in load(path) if item["id"] != clean["id"]]
        _write(path, [clean, *entries][:MAX_ENTRIES])


def get(path: Path, entry_id: str) -> dict | None:
    return next((item for item in load(path) if item["id"] == entry_id), None)


def summary(entry: dict) -> dict:
    """What the history list needs: everything except the heavy per-song data."""
    light = {key: value for key, value in entry.items() if key not in {"songs", "tracks"}}
    light["track_count"] = len(entry.get("tracks") or [])
    return light


def remove(path: Path, entry_id: str) -> None:
    with _lock:
        _write(path, [item for item in load(path) if item["id"] != entry_id])


def clear(path: Path) -> None:
    with _lock:
        _write(path, [])
