"""Deliver the phone's first screen without extra authenticated asset requests."""

from __future__ import annotations

import base64
from pathlib import Path
import re


def render(page: str, static: Path, nonce: str) -> str:
    """Inline our own startup assets, preserving deferred script execution order.

    Safari can otherwise sit on a blank page waiting for the blocking theme
    script. A single authenticated response also avoids depending on a newly
    set pairing cookie for every startup resource. API requests remain gated.
    """
    deferred = []

    def script_tag(name: str) -> str:
        source = (static / name).read_text(encoding="utf-8")
        source = re.sub(r"</script", r"<\\/script", source, flags=re.IGNORECASE)
        return f'<script nonce="{nonce}">{source}</script>'

    def script(match: re.Match) -> str:
        tag = script_tag(match[1])
        if match[2]:
            deferred.append(tag)
            return ""
        return tag

    page = re.sub(r'<script src="/static/([\w.-]+\.js)"( defer)?></script>', script, page)
    css = (static / "style.css").read_text(encoding="utf-8")
    page = page.replace('<link rel="stylesheet" href="/static/style.css">', f"<style>{css}</style>")
    if 'src="/static/logo.svg"' in page:
        logo = base64.b64encode((static / "logo.svg").read_bytes()).decode("ascii")
        page = page.replace('src="/static/logo.svg"', f'src="data:image/svg+xml;base64,{logo}"')
    status = (
        '<div id="phone-startup" role="status" style="margin:16px;padding:16px;'
        'border:1px solid currentColor;border-radius:12px;font:16px/1.5 system-ui">'
        '<span id="phone-startup-message">Opening Crate…</span> '
        '<a id="phone-reload" href="" hidden>Reload page</a>'
        '<noscript>Enable JavaScript in Safari to use Crate.</noscript></div>'
    )
    page = page.replace("<body>", f"<body>{status}", 1)
    return page.replace("</body>", script_tag("phone-startup.js") + "\n" + "\n".join(deferred) + "\n</body>", 1)
