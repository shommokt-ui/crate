"""Copy finished songs into the folder the user chose in the Finder panel."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from downloader import _safe_name

FALLBACK = Path("~/Music/Crate").expanduser()


def resolve_folder(value: str) -> Path:
    """A folder inside the user's home, or on an external drive; anything else falls back to Music/Crate."""
    home = Path.home().resolve()
    try:
        path = Path(value).expanduser().resolve() if value else FALLBACK
    except (OSError, RuntimeError):
        return FALLBACK
    inside = path != home and path.is_relative_to(home) or path.is_relative_to(Path("/Volumes")) and path != Path("/Volumes")
    return path if inside else FALLBACK


def _unique(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix, n = target.stem, target.suffix, 2
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def deliver(files: list[Path], title: str, folder: str, single: bool) -> str:
    base = resolve_folder(folder)
    target = base if single else base / _safe_name(title, "Playlist")
    target.mkdir(parents=True, exist_ok=True)
    for file in files:
        shutil.copy2(file, _unique(target, re.sub(r"^\d{3} - ", "", file.name) if single else file.name))
    shown = str(target).replace(str(Path.home()), "~", 1)
    return f"Saved {len(files)} song" + ("" if len(files) == 1 else "s") + f" to {shown}"
