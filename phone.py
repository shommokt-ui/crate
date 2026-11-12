"""Phone access: a secret pairing code and the address a phone on the same wifi can reach."""

from __future__ import annotations

import ipaddress
import os
import secrets
import socket
import subprocess
from pathlib import Path

import segno


def load_token(path: Path, reset: bool = False) -> str:
    path = Path(path)
    if not reset:
        try:
            token = path.read_text().strip()
            if len(token) >= 20:
                return token
        except OSError:
            pass
    token = secrets.token_urlsafe(24)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w") as file:
        file.write(token)
    return token


def lan_ip() -> str | None:
    """The address of this Mac on its wifi or ethernet (a VPN's address would be unreachable from a phone)."""
    for interface in ("en0", "en1", "en2"):
        try:
            found = subprocess.run(["ipconfig", "getifaddr", interface], capture_output=True, text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        if found:
            return found
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        address = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return None if address.startswith("127.") else address


def host_is_local_network(host: str) -> bool:
    """True for a private IP address or a .local name, so DNS tricks can't point a website at Crate."""
    host = host.strip("[]").lower()
    if host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_link_local or address in ipaddress.ip_network("100.64.0.0/10")


def qr_svg(text: str) -> str:
    svg = segno.make(text, error="m").svg_inline(scale=5, border=2, dark="#111111", light="#ffffff", omitsize=True)
    # The page parses this as an SVG document; without the namespace nothing is drawn.
    return svg.replace("<svg ", '<svg xmlns="http://www.w3.org/2000/svg" ', 1)
