"""Read a public Spotify playlist's tracklist and match each song on SoundCloud.

Spotify audio is DRM-protected and is never downloaded. Only the public track
list (titles, artists, durations) is read; the audio itself comes from the
SoundCloud track that best matches each song.
"""

from __future__ import annotations

import difflib
import json
import re
import ssl
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlsplit

import certifi
import yt_dlp

from downloader import MAX_TRACKS, _duration, _options, _parse_url, _track_url

_HOSTS = {"open.spotify.com"}
_ID = r"[A-Za-z0-9]{22}"
# Words that mark a different recording than the one on the Spotify playlist.
_VARIANTS = {
    "remix", "slowed", "sped", "speed", "nightcore", "cover", "instrumental", "reverb",
    "edit", "mashup", "bootleg", "acoustic", "live", "karaoke", "flip", "mix", "rework",
}
SEARCH_RESULTS = 10
MIN_SCORE = 0.62


def validate_spotify_link(url: str) -> tuple[str, str]:
    """Return ("playlist" | "track", canonical URL) for a public Spotify link."""
    parsed = _parse_url(url)
    path = re.sub(r"^/intl-[a-z]{2}(?:-[A-Za-z]{2,4})?(?=/)", "", parsed.path)
    match = re.fullmatch(rf"/(?:embed/)?(playlist|track)/({_ID})/?", path)
    if parsed.hostname not in _HOSTS or not match:
        raise ValueError("Use a link to a public playlist or song.")
    return match.group(1), f"https://open.spotify.com/{match.group(1)}/{match.group(2)}"


def validate_spotify_url(url: str) -> str:
    parsed = _parse_url(url)
    path = re.sub(r"^/intl-[a-z]{2}(?:-[A-Za-z]{2,4})?(?=/)", "", parsed.path)
    match = re.fullmatch(rf"/(?:embed/)?playlist/({_ID})/?", path)
    if parsed.hostname not in _HOSTS or not match:
        raise ValueError("Use a playlist link like https://open.spotify.com/playlist/…")
    return f"https://open.spotify.com/playlist/{match.group(1)}"


def _safe_cover(entity: dict) -> str | None:
    sources = list((entity.get("coverArt") or {}).get("sources") or [])
    # Single tracks carry their artwork under visualIdentity instead of coverArt.
    sources += (entity.get("visualIdentity") or {}).get("image") or []
    for source in sources:
        url = source.get("url") if isinstance(source, dict) else None
        try:
            parsed = urlsplit(url or "")
        except ValueError:
            continue
        if parsed.scheme == "https" and parsed.hostname and parsed.hostname.endswith((".scdn.co", ".spotifycdn.com")):
            return url
    return None


def _fetch_entity(kind: str, item_id: str) -> dict:
    request = urllib.request.Request(
        f"https://open.spotify.com/embed/{kind}/{item_id}",
        headers={"User-Agent": "Mozilla/5.0 (Macintosh) Crate/1.0", "Accept-Language": "en"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20, context=ssl.create_default_context(cafile=certifi.where())) as response:
            page = response.read(5 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        if error.code in {403, 404}:
            raise ValueError("Couldn't find that link. Check that it is public.") from error
        raise
    found = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
    try:
        entity = json.loads(found.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("Couldn't read that link. Check that it is public.") from error
    if not isinstance(entity, dict) or not (entity.get("trackList") or entity.get("type") == "track"):
        raise ValueError("Couldn't read any songs from that link. Check that it is public.")
    return entity


def preview_spotify(url: str) -> dict:
    kind, canonical = validate_spotify_link(url)
    entity = _fetch_entity(kind, canonical.rsplit("/", 1)[-1])
    if kind == "track":
        artists = ", ".join(a.get("name", "") for a in entity.get("artists") or [] if isinstance(a, dict)) or "Unknown artist"
        entity = {**entity, "subtitle": artists, "trackList": [{**entity, "subtitle": artists}]}
    tracks = []
    for number, item in enumerate(entity["trackList"][:MAX_TRACKS], 1):
        if not isinstance(item, dict):
            continue
        duration = item.get("duration")
        tracks.append({
            "id": str(item.get("uri", f"track-{number}")).rsplit(":", 1)[-1],
            "title": str(item.get("title") or f"Track {number}"),
            "artist": str(item.get("subtitle") or "Unknown artist").replace("\xa0", " "),
            "duration": _duration(duration / 1000) if isinstance(duration, (int, float)) else None,
            "url": "",
            "available": True,
        })
    return {
        "source": "spotify",
        "kind": kind,
        "url": canonical,
        "title": str(entity.get("title") or entity.get("name") or "Spotify playlist"),
        "creator": str(entity.get("subtitle") or "Spotify"),
        "artwork": _safe_cover(entity),
        "track_count": len(tracks),
        "tracks": tracks,
    }


def _words(value: str) -> list[str]:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.findall(r"[a-z0-9]+", value)


def _core_title(title: str) -> str:
    """Drop '(feat. …)' and '- Remastered 2011'-style suffixes before comparing titles."""
    title = re.sub(r"\s*[\(\[](?:feat|ft|with|prod)\b[^\)\]]*[\)\]]", "", title, flags=re.I)
    return re.sub(r"\s+-\s+(?:remaster|single version|radio edit|\d{4}).*$", "", title, flags=re.I)


def _score(candidate: dict, title: str, artist: str, duration: float | None, strict: bool = True) -> float:
    cand_title = candidate.get("title") or ""
    cand_len = candidate.get("duration")
    if duration and isinstance(cand_len, (int, float)):
        if strict and abs(cand_len - duration) > max(5, duration * 0.04):
            return 0.0
        # Relaxed pass: the same song as an extended or radio cut, but not a snippet or a long mix.
        if not strict and not 0.6 <= cand_len / duration <= 1.8:
            return 0.0
    wanted, got = _words(_core_title(title)), _words(cand_title)
    if _VARIANTS & set(got) - set(wanted):
        return 0.0
    artists = set(_words(artist))
    haystack = set(got) | set(_words(candidate.get("uploader") or ""))
    artist_hit = bool(artists) and len(artists & haystack) / len(artists) >= 0.5
    ratio = difflib.SequenceMatcher(None, " ".join(wanted), " ".join(got)).ratio()
    contained = bool(wanted) and set(wanted) <= set(got)
    base = max(ratio, 0.85 if contained else 0.0)
    return base * (1.0 if artist_hit else 0.7) + (0.1 if duration and cand_len else 0)


def find_on_soundcloud(track: dict, exclude: str | None = None, strict_only: bool = False) -> tuple[list[str], str | None]:
    """Return (SoundCloud track URLs, best first, and a note when the match is a different cut).

    `exclude` drops one URL (a locked original, when hunting for a re-upload); `strict_only` refuses
    matches whose length differs, so a re-upload is always the same recording."""
    title, artist, duration = track.get("title", ""), track.get("artist", ""), track.get("duration")
    query = f"scsearch{SEARCH_RESULTS}:{artist.split(',')[0]} {_core_title(title)}"
    options = {**_options(), "extract_flat": True, "allowed_extractors": ["soundcloud", "soundcloud:search"]}
    with yt_dlp.YoutubeDL(options) as ydl:
        entries = [entry or {} for entry in (ydl.extract_info(query, download=False) or {}).get("entries") or []]

    def rank(strict: bool, floor: float) -> list[str]:
        scored = []
        for entry in entries:
            score = _score(entry, title, artist, duration, strict)
            if score >= floor:
                for key in ("webpage_url", "url"):
                    try:
                        found = _track_url(entry.get(key) or "")
                    except ValueError:
                        continue
                    if found != exclude:
                        scored.append((score, found))
                    break
        scored.sort(key=lambda pair: -pair[0])
        return list(dict.fromkeys(url for _, url in scored))[:3]

    urls = rank(True, MIN_SCORE)
    if urls:
        return urls, None
    if strict_only:
        raise ValueError("No match found")
    urls = rank(False, 0.8)  # stricter title/artist bar once the length can't confirm the match
    if urls:
        return urls, "Matched a different length of this song"
    raise ValueError("No match found")


def find_on_youtube(track: dict) -> tuple[list[str], str]:
    """Return (YouTube video URLs, best first, note) for the same recording as `track`."""
    from downloader import _YOUTUBE_EXTRACTORS  # local import: downloader imports this module lazily
    title, artist, duration = track.get("title", ""), track.get("artist", ""), track.get("duration")
    query = f"ytsearch8:{artist.split(',')[0]} {_core_title(title)}"
    options = {**_options(_YOUTUBE_EXTRACTORS), "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(options) as ydl:
        entries = [entry or {} for entry in (ydl.extract_info(query, download=False) or {}).get("entries") or []]

    def rank(strict: bool) -> list[str]:
        scored = []
        for entry in entries:
            video = entry.get("id") or ""
            candidate = {"title": entry.get("title"), "duration": entry.get("duration"),
                         "uploader": entry.get("channel") or entry.get("uploader")}
            score = _score(candidate, title, artist, duration, strict=strict)
            # Music videos run a little longer than the album cut, so the relaxed pass allows that.
            if score >= (MIN_SCORE if strict else 0.8) and re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
                scored.append((score, f"https://www.youtube.com/watch?v={video}"))
        scored.sort(key=lambda pair: -pair[0])
        return list(dict.fromkeys(url for _, url in scored))[:3]

    urls = rank(True) or rank(False)
    if not urls:
        raise ValueError("No match found")
    return urls, "Used a YouTube copy"
