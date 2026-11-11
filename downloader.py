"""SoundCloud playlist inspection and isolated, cancellable ZIP downloads."""

from __future__ import annotations

import copy
import json
import math
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import imageio_ffmpeg
import yt_dlp
from yt_dlp.extractor.soundcloud import SoundcloudIE, SoundcloudSetIE

MAX_TRACKS = 500
# format -> (yt-dlp codec, file extension, lossy?, can carry cover art)
CONVERSIONS = {
    "mp3": ("mp3", ".mp3", True, True),
    "aac": ("m4a", ".m4a", True, True),
    "opus": ("opus", ".opus", True, True),
    "flac": ("flac", ".flac", False, True),
    "wav": ("wav", ".wav", False, False),
}
AUDIO_FORMATS = {"original", *CONVERSIONS}
_HOSTS = {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}
_SLUG = r"[A-Za-z0-9_-]+"
_AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".opus", ".ogg", ".oga", ".flac", ".wav",
    ".aif", ".aiff", ".alac", ".wma", ".mka", ".webm", ".mp4",
}


class DownloadCancelled(Exception):
    """Raised when a job is cancelled, after its temporary files are removed."""


class _QuietLogger:
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


def _parse_url(value: str):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Paste a full SoundCloud playlist link.")
    value = value.strip()
    if not value or re.search(r"[\x00-\x20\x7f\\]", value):
        raise ValueError("The SoundCloud link contains invalid characters.")
    try:
        parsed = urlsplit(value)
        invalid = (
            parsed.scheme.lower() not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or ":" in parsed.netloc
            or "#" in value
        )
    except ValueError as error:
        raise ValueError("Enter a valid SoundCloud link.") from error
    if invalid:
        raise ValueError("Use a public SoundCloud link without credentials, a port, or a fragment.")
    return parsed


def validate_playlist_url(url: str) -> str:
    """Allow only public /artist/sets/playlist links; remove sharing parameters."""
    parsed = _parse_url(url)
    if parsed.hostname not in _HOSTS or not re.fullmatch(
        rf"/{_SLUG}/sets/{_SLUG}/?", parsed.path
    ):
        raise ValueError("Use a playlist link like https://soundcloud.com/artist/sets/playlist.")
    if any(key.lower() in {"secret_token", "token"} for key in parse_qs(parsed.query, keep_blank_values=True)):
        raise ValueError("Private playlists are not supported. Use a public playlist link.")
    return "https://soundcloud.com" + parsed.path.rstrip("/")


def classify_soundcloud_url(url: str) -> tuple[str, str]:
    """Return ("playlist" | "track", canonical URL) for a public SoundCloud link."""
    try:
        return "playlist", validate_playlist_url(url)
    except ValueError as playlist_error:
        parsed = _parse_url(url)
        if parsed.hostname in _HOSTS and not any(
            key.lower() in {"secret_token", "token"} for key in parse_qs(parsed.query, keep_blank_values=True)
        ):
            try:
                return "track", _track_url(f"https://soundcloud.com{parsed.path}")
            except ValueError:
                pass
        raise ValueError("Use a link to a public playlist or song.") from playlist_error


_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
_YOUTUBE_EXTRACTORS = ("youtube", "youtube:tab", "youtube:search")
_VIDEO_ID = r"[A-Za-z0-9_-]{11}"


def classify_youtube_url(url: str) -> tuple[str, str]:
    """Return ("track" | "playlist", canonical URL) for a YouTube link."""
    parsed = _parse_url(url)
    if parsed.hostname not in _YOUTUBE_HOSTS:
        raise ValueError("Use a link to a YouTube video or playlist.")
    query = parse_qs(parsed.query)
    video = None
    if parsed.hostname == "youtu.be":
        match = re.fullmatch(rf"/({_VIDEO_ID})/?", parsed.path)
        video = match.group(1) if match else None
    elif parsed.path in {"/watch", "/watch/"}:
        candidate = (query.get("v") or [""])[0]
        video = candidate if re.fullmatch(_VIDEO_ID, candidate) else None
    else:
        match = re.fullmatch(rf"/(?:shorts|live|embed)/({_VIDEO_ID})/?", parsed.path)
        video = match.group(1) if match else None
    if video:  # a video link wins even when it carries a ?list= parameter
        return "track", f"https://www.youtube.com/watch?v={video}"
    if parsed.path.rstrip("/") == "/playlist":
        listing = (query.get("list") or [""])[0]
        if re.fullmatch(r"[A-Za-z0-9_-]{10,64}", listing):
            if listing.startswith(("RD", "UL")):
                raise ValueError("Auto-generated mixes can't be downloaded. Use a regular playlist link.")
            return "playlist", f"https://www.youtube.com/playlist?list={listing}"
    raise ValueError("Use a link to a YouTube video or playlist.")


def _youtube_video_url(url: str) -> str:
    kind, canonical = classify_youtube_url(url)
    if kind != "track":
        raise ValueError("The playlist contains an unsupported video link.")
    return canonical


def _safe_youtube_image(value) -> str | None:
    try:
        parsed = urlsplit(value or "")
    except ValueError:
        return None
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith((".ytimg.com", ".ggpht.com")):
        return value
    return None


def _youtube_names(title: str, channel: str) -> tuple[str, str]:
    """Split "Artist - Song (Official Video)" into artist and song; otherwise use the channel name."""
    title = re.sub(r"\s*[\(\[](?:official|lyric|lyrics|music video|audio|video|hd|4k|visuali[sz]er)[^\)\]]*[\)\]]", "", title, flags=re.I).strip()
    channel = re.sub(r"\s*-\s*Topic$", "", channel or "", flags=re.I).strip() or "Unknown artist"
    if " - " in title:
        artist, song = title.split(" - ", 1)
        if artist.strip() and song.strip():
            return artist.strip(), song.strip()
    return channel, title or "Video"


def preview_youtube(url: str) -> dict:
    kind, canonical = classify_youtube_url(url)
    if kind == "track":
        with yt_dlp.YoutubeDL({**_options(_YOUTUBE_EXTRACTORS), "noplaylist": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(canonical, download=False)
        if not info or info.get("_type") == "playlist":
            raise ValueError("That link isn't a single video.")
        artist, title = _youtube_names(str(info.get("title") or ""), str(info.get("artist") or info.get("channel") or info.get("uploader") or ""))
        artwork = _safe_youtube_image(f"https://i.ytimg.com/vi/{info.get('id')}/hqdefault.jpg")
        return {
            "source": "youtube", "kind": "track", "url": canonical, "title": title, "creator": artist, "artwork": artwork,
            "track_count": 1,
            "tracks": [{"id": str(info.get("id") or "1"), "title": title, "artist": artist, "duration": _duration(info.get("duration")),
                        "url": canonical, "available": True, "reason": None}],
        }
    options = {**_options(_YOUTUBE_EXTRACTORS), "extract_flat": "in_playlist", "playlistend": MAX_TRACKS + 1, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(canonical, download=False)
    entries = [entry for entry in (info or {}).get("entries") or [] if entry]
    if not entries:
        raise ValueError("This playlist has no accessible videos. Check that it is public.")
    if len(entries) > MAX_TRACKS:
        raise ValueError(f"Playlists can contain up to {MAX_TRACKS} videos.")
    tracks = []
    for number, entry in enumerate(entries, 1):
        video_id = str(entry.get("id") or "")
        raw_title = str(entry.get("title") or f"Video {number}")
        gone = re.fullmatch(r"\[(private|deleted) video\]", raw_title.strip(), re.I)
        artist, title = _youtube_names(raw_title, str(entry.get("channel") or entry.get("uploader") or ""))
        tracks.append({
            "id": video_id or f"unavailable-{number}", "title": title, "artist": artist, "duration": _duration(entry.get("duration")),
            "url": f"https://www.youtube.com/watch?v={video_id}" if re.fullmatch(_VIDEO_ID, video_id) and not gone else "",
            "available": bool(re.fullmatch(_VIDEO_ID, video_id)) and not gone,
            "reason": f"{gone.group(1).capitalize()} video" if gone else (None if re.fullmatch(_VIDEO_ID, video_id) else "Unavailable"),
        })
    first = next((t["id"] for t in tracks if t["available"]), None)
    return {
        "source": "youtube", "kind": "playlist", "url": canonical, "title": str(info.get("title") or "YouTube playlist"),
        "creator": str(info.get("channel") or info.get("uploader") or "YouTube"),
        "artwork": _safe_youtube_image(f"https://i.ytimg.com/vi/{first}/hqdefault.jpg") if first else None,
        "track_count": len(tracks), "tracks": tracks,
    }


def looks_like_search(text: str) -> bool:
    """Plain words (no scheme, not a bare domain) are treated as a song search."""
    text = text.strip()
    return 3 <= len(text) <= 150 and "://" not in text and not re.match(r"^[\w.-]+\.[A-Za-z]{2,}(/|$)", text)


def preview_search(query: str) -> dict:
    """Find the best match for a typed song name on YouTube and describe it like a pasted video link."""
    from spotify import _VARIANTS, _words
    wanted = _words(query)
    options = {**_options(_YOUTUBE_EXTRACTORS), "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        entries = [entry or {} for entry in (ydl.extract_info(f"ytsearch10:{query}", download=False) or {}).get("entries") or []]
    best, best_score = None, 0.0
    for rank, entry in enumerate(entries):
        video = entry.get("id") or ""
        duration = entry.get("duration")
        if not re.fullmatch(_VIDEO_ID, video) or not isinstance(duration, (int, float)) or not 60 <= duration <= 900:
            continue  # skip live streams, shorts and hour-long mixes
        title = str(entry.get("title") or "")
        channel = str(entry.get("channel") or entry.get("uploader") or "")
        have = set(_words(title)) | set(_words(channel))
        coverage = sum(1 for word in wanted if word in have) / max(1, len(wanted))
        if coverage < 0.7 or (_VARIANTS & set(_words(title))) - set(wanted):
            continue  # missing words from the query, or a remix/cover/slowed version nobody asked for
        score = coverage + (0.15 if channel.lower().endswith("- topic") else 0) + (0.1 if re.search(r"official (audio|video)", title, re.I) else 0) - rank * 0.01
        if score > best_score:
            best, best_score = video, score
    if not best:
        raise ValueError("Couldn't find that song. Try adding the artist's name, or paste a link.")
    return preview_youtube(f"https://www.youtube.com/watch?v={best}")


def _track_url(url: str) -> str:
    parsed = _parse_url(url)
    if parsed.query:
        raise ValueError("Private or unexpected track links are not supported.")
    path = parsed.path.rstrip("/")
    if parsed.hostname in _HOSTS and re.fullmatch(rf"/{_SLUG}/{_SLUG}", path):
        if path.rsplit("/", 1)[-1] in {"tracks", "albums", "sets", "reposts", "likes", "spotlight", "comments"}:
            raise ValueError("The playlist entry is not a track.")
        return "https://soundcloud.com" + path
    if parsed.hostname in {"api.soundcloud.com", "api-v2.soundcloud.com"}:
        match = re.fullmatch(r"/tracks/(?:soundcloud%3Atracks%3A)?([0-9]+)", path, re.IGNORECASE)
        if match:
            return f"https://api-v2.soundcloud.com/tracks/{match.group(1)}"
    raise ValueError("The playlist contains an unsupported track link.")


def _ffmpeg_path() -> str | None:
    try:
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).is_file() and os.access(bundled, os.X_OK):
            return bundled
    except (RuntimeError, OSError) as exc:
        logging.getLogger(__name__).warning("Bundled FFmpeg unavailable: %r", exc)
    return shutil.which("ffmpeg")


def get_capabilities() -> dict:
    available = _ffmpeg_path() is not None
    return {"mp3_available": available, "ffmpeg_available": available}


def _options(extractors: tuple[str, ...] = ("soundcloud", "soundcloud:set")) -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietLogger(),
        "noprogress": True,
        "cachedir": False,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "extractor_retries": 2,
        "skip_unavailable_fragments": False,
        "concurrent_fragment_downloads": 1,
        "allowed_extractors": list(extractors),
        "usenetrc": False,
    }


def _duration(value):
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return round(value, 3)
    return None


def _safe_artwork(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.hostname.endswith(".sndcdn.com")
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
        ):
            return value
    except ValueError:
        pass
    return None


def restriction_reason(track: dict) -> str | None:
    """Say why SoundCloud's track data shows this track can't be downloaded in full."""
    if track.get("sharing") == "private":
        return "Private track"
    if track.get("policy") == "BLOCK":
        return "Blocked in your region"
    if track.get("policy") == "SNIP":
        return "Only a 30-second preview is available"
    if track.get("streamable") is False and not track.get("downloadable"):
        return "The uploader doesn't allow downloads"
    transcodings = (track.get("media") or {}).get("transcodings") or []
    if transcodings and not track.get("downloadable"):
        playable = [
            item for item in transcodings if isinstance(item, dict)
            and not item.get("snipped") and "/preview/" not in str(item.get("url", ""))
        ]
        if not playable:
            return "Only a 30-second preview is available"
        if all(str((item.get("format") or {}).get("protocol", "")).startswith(("ctr-", "cbc-")) for item in playable):
            return "Locked by SoundCloud"
    return None


def _raw_track_available(track: dict) -> bool:
    if track.get("policy") in {"SNIP", "BLOCK"} or track.get("sharing") == "private":
        return False
    if track.get("streamable") is False and not track.get("downloadable"):
        return False
    transcodings = (track.get("media") or {}).get("transcodings") or []
    if transcodings and not track.get("downloadable"):
        # Missing media data is unknown. Known DRM-only or preview-only media
        # can be labelled unavailable before a download is attempted.
        return any(
            not str((item.get("format") or {}).get("protocol", "")).startswith(("ctr-", "cbc-"))
            and not item.get("snipped")
            and "/preview/" not in str(item.get("url", ""))
            for item in transcodings if isinstance(item, dict)
        )
    return True


class _PlaylistMetadataIE(SoundcloudSetIE):
    """Retain metadata discarded by yt-dlp's standard flat playlist output.

    Large public playlists contain ID-only entries. The same tracks endpoint
    used by yt-dlp can resolve those IDs in batches without fetching any audio
    formats, keeping preview independent of the number of media requests.
    """

    def _extract_set(self, playlist, token=None):
        if token or playlist.get("sharing") == "private":
            raise ValueError("Private playlists are not supported.")
        tracks = playlist.get("tracks") or []
        declared_count = playlist.get("track_count") or len(tracks)
        if len(tracks) > MAX_TRACKS or declared_count > MAX_TRACKS:
            raise ValueError(f"Playlists can contain up to {MAX_TRACKS} tracks.")
        missing = [str(t["id"]) for t in tracks if t.get("id") and not t.get("title")]
        metadata = {}
        for start in range(0, len(missing), 50):
            batch = [track_id for track_id in missing[start:start + 50] if track_id.isdecimal()]
            if not batch:
                continue
            resolved = self._call_api(
                self._API_V2_BASE + "tracks", str(playlist["id"]),
                "Loading track titles", query={"ids": ",".join(batch)}, fatal=False,
            )
            if isinstance(resolved, list):
                metadata.update({str(track["id"]): track for track in resolved if isinstance(track, dict) and track.get("id")})
        entries = []
        for number, original in enumerate(tracks, 1):
            track = {**original, **metadata.get(str(original.get("id")), {})}
            track_id = str(track.get("id") or f"unavailable-{number}")
            track_url = track.get("permalink_url")
            if not track_url and track_id.isdecimal():
                track_url = f"https://api-v2.soundcloud.com/tracks/{track_id}"
            available = bool(track_url) and _raw_track_available(track)
            entry = self.url_result(
                track_url or "", ie="Soundcloud", video_id=track_id,
                video_title=track.get("title") or f"Track {number}",
            )
            entry.update({
                "uploader": (track.get("user") or {}).get("username"),
                "duration": _duration(track.get("duration", 0) / 1000) if isinstance(track.get("duration"), (int, float)) else None,
                "availability": "public" if available else "unavailable",
                "restriction": restriction_reason(track) if not available else None,
                "thumbnail": track.get("artwork_url"),
            })
            entries.append(entry)
        return self.playlist_result(
            entries, str(playlist["id"]), playlist.get("title"),
            uploader=(playlist.get("user") or {}).get("username"),
            thumbnail=playlist.get("artwork_url") or (playlist.get("user") or {}).get("avatar_url"),
        )


class _TrackMetadataIE(SoundcloudIE):
    """Read a song's metadata without resolving audio formats.

    The stock extractor raises on encrypted-only tracks before returning
    anything, which would hide the title and the reason it is locked.
    """

    def _extract_info_dict(self, info, full_title=None, secret_token=None, extract_flat=False):
        result = super()._extract_info_dict(info, full_title, secret_token, extract_flat=True)
        result["restriction"] = restriction_reason(info) if not _raw_track_available(info) else None
        # The flat result has no thumbnail; take the song's cover (or its uploader's picture) from the raw record.
        cover = info.get("artwork_url") or (info.get("user") or {}).get("avatar_url")
        result["cover"] = cover.replace("-large.", "-t500x500.") if isinstance(cover, str) else None
        return result


def preview_playlist(url: str) -> dict:
    canonical = validate_playlist_url(url)
    options = {**_options(), "extract_flat": "in_playlist", "playlistend": MAX_TRACKS + 1}
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.add_info_extractor(_PlaylistMetadataIE())
        info = ydl.extract_info(canonical, download=False, ie_key=_PlaylistMetadataIE.ie_key())
    if not info or info.get("_type") != "playlist":
        raise ValueError("SoundCloud did not return a playlist. Check that the link is public.")
    entries = list(info.get("entries") or [])
    if len(entries) > MAX_TRACKS or (info.get("playlist_count") or 0) > MAX_TRACKS:
        raise ValueError(f"Playlists can contain up to {MAX_TRACKS} tracks.")
    if not entries:
        raise ValueError("This playlist has no accessible tracks.")
    tracks = []
    for number, entry in enumerate(entries, 1):
        entry = entry or {}
        available = entry.get("availability") not in {"unavailable", "private", "premium_only", "subscriber_only", "needs_auth"}
        try:
            track_url = _track_url(entry.get("webpage_url") or entry.get("url") or "")
        except ValueError:
            track_url, available = "", False
        tracks.append({
            "id": str(entry.get("id") or f"unavailable-{number}"),
            "title": str(entry.get("title") or f"Track {number}"),
            "artist": str(entry.get("artist") or entry.get("uploader") or "Unknown artist"),
            "duration": _duration(entry.get("duration")),
            "url": track_url,
            "available": available,
            "reason": entry.get("restriction") if not available else None,
        })
    artwork = _safe_artwork(info.get("thumbnail"))
    if not artwork:
        artwork = next((_safe_artwork(entry.get("thumbnail")) for entry in entries if entry and _safe_artwork(entry.get("thumbnail"))), None)
    return {
        "url": canonical,
        "title": str(info.get("title") or "SoundCloud playlist"),
        "creator": str(info.get("uploader") or info.get("album_artist") or "SoundCloud"),
        "artwork": artwork,
        "track_count": len(tracks),
        "tracks": tracks,
    }


def preview_track(url: str) -> dict:
    """Describe one SoundCloud song, shaped like a one-track playlist."""
    canonical = _track_url(url)
    with yt_dlp.YoutubeDL({**_options(), "noplaylist": True}) as ydl:
        ydl.add_info_extractor(_TrackMetadataIE())
        info = ydl.extract_info(canonical, download=False, process=False, ie_key=_TrackMetadataIE.ie_key())
    if not info or info.get("_type", "video") not in {"video", "url"}:
        raise ValueError("That link isn't a single song.")
    reason = info.get("restriction")
    artist = str(info.get("artist") or info.get("uploader") or "Unknown artist")
    title = str(info.get("title") or "Song")
    return {
        "kind": "track",
        "url": canonical,
        "title": title,
        "creator": artist,
        "artwork": _safe_artwork(info.get("cover") or info.get("thumbnail")),
        "track_count": 1,
        "tracks": [{
            "id": str(info.get("id") or "1"),
            "title": title,
            "artist": artist,
            "duration": _duration(info.get("duration")),
            "url": canonical,
            "available": reason is None,
            "reason": reason,
        }],
    }


def _safe_name(value: str, default: str = "Untitled") -> str:
    value = unicodedata.normalize("NFKC", str(value))
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    value = value.encode("utf-8")[:160].decode("utf-8", errors="ignore").strip(" .") or default
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "_" + value
    return value


def _clean_error(error: Exception) -> str:
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(error))
    if "DRM" in message:
        return "Locked by SoundCloud"
    if re.search(r"sign in to confirm|not a bot", message, re.I):
        return "YouTube wants a sign-in for this video"
    if re.search(r"private video", message, re.I):
        return "Private video"
    if re.search(r"video unavailable|not available|removed|terminated", message, re.I):
        return "Video unavailable"
    if re.search(r"age|inappropriate|confirm your age", message, re.I) and re.search(r"restrict|verify|confirm", message, re.I):
        return "Age-restricted: YouTube requires sign-in"
    message = re.sub(r"\[youtube[^\]]*\]\s*\S+:\s*", "", message)
    message = re.sub(r"https?://\S+", "[link]", message)
    return re.sub(r"\s+", " ", message).removeprefix("ERROR: ")[:350] or "This track could not be downloaded."


def _full_audio_formats(info: dict) -> list:
    if info.get("policy") in {"SNIP", "BLOCK"} or info.get("availability") in {"private", "premium_only", "subscriber_only", "needs_auth"}:
        return []
    return [
        fmt for fmt in info.get("formats") or []
        if not fmt.get("has_drm")
        and "preview" not in str(fmt.get("format_id", "")).lower()
        and "preview" not in str(fmt.get("format_note", "")).lower()
        and not fmt.get("snipped")
        and "/preview/" not in str(fmt.get("url", ""))
    ]


def _youtube_audio_formats(info: dict) -> list:
    """Keep real audio streams (not storyboards or video-only), never DRM-protected ones."""
    return [
        fmt for fmt in info.get("formats") or []
        if fmt.get("acodec") not in (None, "none") and not fmt.get("has_drm") and fmt.get("protocol") != "mhtml"
    ]


def _completed_audio(track_dir: Path, audio_format: str) -> Path:
    files = [
        file for file in track_dir.iterdir()
        if file.is_file() and not file.is_symlink()
        and file.suffix.lower() in _AUDIO_EXTENSIONS
        and (audio_format not in CONVERSIONS or file.suffix.lower() == CONVERSIONS[audio_format][1])
    ]
    if len(files) != 1 or files[0].stat().st_size == 0:
        raise RuntimeError("The download did not produce one complete audio file.")
    return files[0]


def download_playlist(
    preview: dict,
    job_dir: Path,
    audio_format: str,
    on_update: Callable[[dict], None],
    cancel_event: threading.Event,
    options: dict | None = None,
) -> Path:
    """Download playable tracks in order and return one ZIP containing them."""
    if audio_format not in AUDIO_FORMATS:
        raise ValueError("Choose a supported audio format.")
    prefs = options or {}
    quality = str(prefs.get("quality", 320))
    numbering, tags, cover = prefs.get("numbering", True), prefs.get("tags", True), prefs.get("cover", True)
    from_spotify = preview.get("source") == "spotify"
    from_youtube = preview.get("source") == "youtube"
    single = preview.get("kind") == "track"
    if from_youtube:
        canonical = classify_youtube_url(preview.get("url", ""))[1]
    elif from_spotify:
        from spotify import validate_spotify_link
        canonical = validate_spotify_link(preview.get("url", ""))[1]
    elif single:
        canonical = _track_url(preview.get("url", ""))
    else:
        canonical = validate_playlist_url(preview.get("url", ""))
    source_tracks = preview.get("tracks") or []
    if not source_tracks or len(source_tracks) > MAX_TRACKS or (single and len(source_tracks) != 1):
        raise ValueError(f"Choose a playlist with 1 to {MAX_TRACKS} tracks.")
    ffmpeg = _ffmpeg_path()
    if audio_format != "original" and not ffmpeg:
        raise ValueError("Audio conversion is unavailable. Reinstall dependencies or choose Original audio.")
    job_dir = Path(job_dir).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    work_dir = job_dir / "audio"
    # A job directory belongs to exactly one download; avoid including stale files.
    work_dir.mkdir(exist_ok=False)
    zip_name = _safe_name(preview.get("title") or "SoundCloud playlist") + ".zip"
    zip_path = job_dir / zip_name
    temporary_zip = job_dir / (zip_name + ".part")
    if zip_path.exists() or temporary_zip.exists():
        work_dir.rmdir()
        raise ValueError("This job already has an archive. Start a new download.")
    total = len(source_tracks)
    tracks = [
        {"id": str(track.get("id", number)), "title": str(track.get("title") or f"Track {number}"),
         "artist": str(track.get("artist") or "Unknown artist"), "duration": _duration(track.get("duration")),
         "status": "pending"}
        for number, track in enumerate(source_tracks, 1)
    ]
    completed = failed = 0
    archive_files = []

    def check_cancelled():
        if cancel_event.is_set():
            raise DownloadCancelled("Download cancelled.")

    def emit(message, current=0, fraction=0, status="downloading"):
        on_update({
            "status": status, "message": message, "current_track": current,
            "total_tracks": total, "completed_tracks": completed, "failed_tracks": failed,
            "progress": round(min(98, ((completed + failed + fraction) / total) * 95), 1) if status == "downloading" else 98,
            "tracks": copy.deepcopy(tracks),
        })

    try:
        check_cancelled()
        emit("Starting your playlist download…")
        for index, (track, row) in enumerate(zip(source_tracks, tracks), 1):
            check_cancelled()
            track_dir = work_dir / f"{index:03d}"
            track_dir.mkdir()
            try:
                locked_reason = None if track.get("available", True) else (track.get("reason") or "Not available for download.")
                row["status"] = "downloading"
                if locked_reason and not from_spotify and not from_youtube:
                    candidate_urls = []  # the original is locked; look for an open re-upload below
                elif from_spotify:
                    from spotify import find_on_soundcloud, find_on_youtube
                    emit(f"Finding {row['title']}", index)
                    try:
                        candidate_urls, match_note = find_on_soundcloud(track)
                    except ValueError:
                        candidate_urls, match_note = find_on_youtube(track)  # not on SoundCloud: use YouTube instead
                    if match_note:
                        row["note"] = match_note
                else:
                    candidate_urls = [(_youtube_video_url if from_youtube else _track_url)(track.get("url") or "")]
                if locked_reason and from_spotify:
                    locked_reason = None
                emit(f"Downloading {row['title']}", index)
                last_progress = 0.0

                def progress_hook(progress):
                    nonlocal last_progress
                    check_cancelled()
                    now = time.monotonic()
                    if now - last_progress < 0.2 and progress.get("status") != "finished":
                        return
                    last_progress = now
                    size = progress.get("total_bytes") or progress.get("total_bytes_estimate")
                    fraction = min(0.95, progress.get("downloaded_bytes", 0) / size) if size else 0
                    emit(f"Downloading {row['title']}", index, fraction)

                def postprocessor_hook(progress):
                    check_cancelled()
                    emit(f"Preparing {row['title']}", index, 0.98)

                # Only generated, escaped filenames enter yt-dlp's template syntax.
                filename = (f"{index:03d} - " if numbering else "") + _safe_name(f"{row['artist']} - {row['title']}")
                options = {
                    **_options(), "format": "bestaudio/best", "noplaylist": True,
                    "outtmpl": str(track_dir / filename).replace("%", "%%") + ".%(ext)s",
                    "progress_hooks": [progress_hook], "postprocessor_hooks": [postprocessor_hook],
                    "overwrites": False, "continuedl": False, "keepvideo": False,
                }
                if ffmpeg:
                    options["ffmpeg_location"] = ffmpeg
                processors = []
                cover_ok = False
                if audio_format in CONVERSIONS:
                    codec, _, lossy, cover_ok = CONVERSIONS[audio_format]
                    extract = {"key": "FFmpegExtractAudio", "preferredcodec": codec}
                    if lossy:
                        extract["preferredquality"] = quality
                    processors.append(extract)
                elif audio_format == "original":
                    cover_ok = True  # embedded when the source container allows it; retried without if not
                if tags:
                    processors.append({"key": "FFmpegMetadata", "add_metadata": True})
                cover_steps = [
                    {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                    {"key": "EmbedThumbnail", "already_have_thumbnail": False},
                ]

                def run_download(use_cover):
                    attempt = {**options, "allowed_extractors": ["soundcloud", "soundcloud:set", *_YOUTUBE_EXTRACTORS], "postprocessors": processors + (cover_steps if use_cover else [])}
                    if use_cover:
                        attempt["writethumbnail"] = True
                    if not attempt["postprocessors"]:
                        del attempt["postprocessors"]
                    with yt_dlp.YoutubeDL(attempt) as ydl:
                        # Spotify matches can be preview-only or protected; fall through to the next-best match.
                        failure = None
                        for track_url in candidate_urls:
                            try:
                                on_youtube = urlsplit(track_url).hostname in _YOUTUBE_HOSTS
                                info = ydl.extract_info(track_url, download=False, process=False, ie_key="Youtube" if on_youtube else "Soundcloud")
                                check_cancelled()
                                if not info or info.get("_type", "video") != "video":
                                    raise ValueError("That link did not return an audio track.")
                                formats = _youtube_audio_formats(info) if on_youtube else _full_audio_formats(info)
                                if not formats:
                                    raise ValueError("No audio available for this video" if on_youtube else "Locked by SoundCloud, or only a preview")
                                info["formats"] = formats
                                ydl.process_ie_result(info, download=True)
                                return None
                            except DownloadCancelled:
                                raise
                            except Exception as error:
                                check_cancelled()
                                failure = error
                                shutil.rmtree(track_dir, ignore_errors=True)
                                track_dir.mkdir()
                        return failure

                failure = ValueError(locked_reason) if not candidate_urls else run_download(bool(cover and cover_ok))
                if failure and candidate_urls and cover and cover_ok and re.search(r"postprocess|embed|thumbnail", str(failure), re.I):
                    failure = run_download(False)  # some containers can't carry cover art; keep the song instead
                if failure and not from_youtube and re.search(r"private|blocked|region|country|preview|protected|encrypted|locked|drm|doesn.t allow|streaming turned off|no match", str(failure), re.I):
                    # The original can't be downloaded: look for the same recording elsewhere, SoundCloud re-uploads first,
                    # then YouTube. Only same-length copies of the same song are used.
                    from spotify import find_on_soundcloud, find_on_youtube
                    emit(f"Looking for another copy of {row['title']}", index)
                    original = _track_url(track.get("url") or "") if (track.get("url") and not from_spotify) else None
                    alternatives, note = [], None
                    try:
                        alternatives, _ = find_on_soundcloud(track, exclude=original, strict_only=True)
                        note = "Used another upload of this song"
                    except ValueError:
                        pass
                    try:
                        found, youtube_note = find_on_youtube(track)
                        alternatives += [url for url in found if url not in alternatives]
                        note = note or youtube_note
                    except ValueError:
                        pass
                    if not alternatives:
                        raise ValueError(f"{_clean_error(failure)}. No other copy found.") from None
                    candidate_urls = alternatives
                    row["note"] = note
                    failure = run_download(bool(cover and cover_ok))
                    if failure and "youtube.com" in alternatives[-1] and note != "Used a YouTube copy":
                        row["note"] = "Used a YouTube copy"
                if failure:
                    raise failure
                check_cancelled()
                file = _completed_audio(track_dir, audio_format)
                archive_files.append((file, file.name))
                row["status"] = "complete"
                row["filename"] = file.name
                completed += 1
            except DownloadCancelled:
                raise
            except Exception as error:
                check_cancelled()
                failed += 1
                row["status"] = "failed"
                row["error"] = _clean_error(error)
                shutil.rmtree(track_dir, ignore_errors=True)
            emit(f"{completed} downloaded · {failed} skipped", index)
        check_cancelled()
        if not archive_files:
            raise RuntimeError("No tracks could be downloaded. " + (tracks[0].get("error") or "Try another public playlist."))
        if "folder" in prefs:  # app downloads always pass settings; blank means the default folder
            from delivery import deliver  # local import: delivery imports this module
            try:
                emit("Saving to your folder…", total, status="packing")
                on_update({"delivered": deliver([f for f, _ in archive_files], preview.get("title") or "",
                                                prefs["folder"], single)})
            except Exception as error:  # the ZIP below is still there as a fallback
                on_update({"delivery_failed": str(error)[:300] or "Couldn't save the songs."})
        if single:
            # One song is delivered as the audio file itself, not a ZIP.
            file, _ = archive_files[0]
            final = job_dir / re.sub(r"^\d{3} - ", "", file.name)
            file.replace(final)
            return final
        emit("Packing your playlist into one ZIP…", total, status="packing")
        manifest = {
            "title": preview.get("title"), "creator": preview.get("creator"), "url": canonical,
            "audio_format": audio_format, "created_at": datetime.now(timezone.utc).isoformat(),
            "total_tracks": total, "completed_tracks": completed, "failed_tracks": failed, "tracks": tracks,
        }
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for file, archive_name in archive_files:
                check_cancelled()
                # Chunked writes keep cancellation responsive while packing large files.
                with file.open("rb") as source, archive.open(archive_name, "w", force_zip64=True) as destination:
                    while chunk := source.read(1024 * 1024):
                        check_cancelled()
                        destination.write(chunk)
            archive.writestr("playlist.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            if failed:
                report = "Skipped tracks\n\n" + "\n".join(
                    f"{number:03d}. {row['artist']} - {row['title']}: {row['error']}"
                    for number, row in enumerate(tracks, 1) if row["status"] == "failed"
                )
                archive.writestr("skipped-tracks.txt", report)
        check_cancelled()
        temporary_zip.replace(zip_path)
        return zip_path
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        temporary_zip.unlink(missing_ok=True)
