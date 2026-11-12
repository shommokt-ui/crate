"""Persistent, validated user settings (stored as one small JSON file)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEFAULTS = {
    "theme": "system",       # system | light | dark
    "visuals": True,         # effect that follows the pointer
    "format": "mp3",         # mp3 | aac | opus | flac | wav | original
    "quality": 320,          # kbps for MP3, AAC and Opus
    "numbering": True,       # "001 - " filename prefix
    "tags": True,            # write title/artist into the files
    "cover": True,           # embed cover art
    "history": True,         # remember what was downloaded
    "skip_downloaded": True, # untick songs that are already in the history
    "autostart": False,      # download as soon as a link loads
    "phone": False,          # let a phone on the same wifi use Crate
    "clear": True,           # clear the link box after finishing
    "folder": "",            # chosen in the Finder panel; empty means ~/Music/Crate
}
_CHOICES = {
    "theme": {"system", "light", "dark"},
    "format": {"mp3", "aac", "opus", "flac", "wav", "original"},
    "quality": {128, 192, 256, 320},
}


def sanitize(data: object) -> dict:
    """Keep only known keys with valid values; anything else falls back to the default."""
    clean = dict(DEFAULTS)
    if not isinstance(data, dict):
        return clean
    for key, default in DEFAULTS.items():
        value = data.get(key, default)
        if key in _CHOICES:
            # Check type before set membership: lists/dicts from malformed JSON
            # are unhashable, and bool/float values can compare equal to ints.
            ok = type(value) is type(default) and value in _CHOICES[key]
        elif isinstance(default, str):
            ok = isinstance(value, str) and len(value.strip()) <= 300
        else:
            ok = isinstance(value, bool)
        clean[key] = value if ok else default
    return clean


def load(path: Path) -> dict:
    try:
        return sanitize(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        return dict(DEFAULTS)


def save(path: Path, data: object) -> dict:
    clean = sanitize(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
    try:
        with os.fdopen(handle, "w") as file:
            json.dump(clean, file, indent=2)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return clean
