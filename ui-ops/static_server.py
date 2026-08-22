"""Minimal static shell server with deployment-controlled framing headers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(os.environ.get("STATIC_ROOT", "/app")).resolve()
BFF = os.environ.get("PORTAL_BFF_ORIGIN", "").rstrip("/")
STUB_EMBEDS = os.environ.get("PORTAL_SELFTEST_STUB_EMBEDS") == "1"
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
_INLINE_SCRIPT = re.compile(r"<script(?:\s[^>]*)?>(.*?)</script>", re.DOTALL | re.IGNORECASE)


def script_sources() -> str:
    """Allow only external same-origin scripts and exact inline build artifacts."""
    sources = {"'self'"}
    for page in ROOT.rglob("*.html") if ROOT.is_dir() else ():
        markup = page.read_text(encoding="utf-8")
        for content in _INLINE_SCRIPT.findall(markup):
            if content:
                digest = base64.b64encode(hashlib.sha256(content.encode()).digest()).decode()
                sources.add(f"'sha256-{digest}'")
    return " ".join(sorted(sources))


def _valid_frame_ancestors(parts: list[str]) -> bool:
    if not parts:
        return False
    for part in parts:
        if part == "'self'":
            continue
        parsed = urlsplit(part)
        try:
            _ = parsed.port
        except ValueError:
            return False
        if (
            part == "*"
            or part != part.lower()
            or parsed.scheme != "https"
            or not parsed.hostname
            or not _HOST.fullmatch(parsed.hostname.lower())
            or ".." in parsed.hostname
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return False
    return True


def frame_ancestors(host: str = "") -> str:
    policies_raw = os.environ.get("TENANT_EMBED_POLICIES_JSON", "").strip()
    if policies_raw:
        try:
            policies = json.loads(policies_raw)
            canonical_host = host.split(":", 1)[0].strip().lower().rstrip(".")
            matches = [
                policy
                for policy in policies.values()
                if canonical_host in {item.strip().lower().rstrip(".") for item in policy["hosts"]}
            ]
            if len(matches) != 1:
                return "'none'"
            parts = list(matches[0]["frame_ancestors"])
            return " ".join(parts) if _valid_frame_ancestors(parts) else "'none'"
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return "'none'"
    value = os.environ.get("FRAME_ANCESTORS", "'self'").strip()
    parts = value.split()
    if not _valid_frame_ancestors(parts):
        raise ValueError("FRAME_ANCESTORS must contain exact HTTPS origins or 'self'")
    return value


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'self'; script-src {script_sources()}; "
            f"style-src 'self' 'unsafe-inline'; frame-src 'self'; "
            f"frame-ancestors {frame_ancestors(self.headers.get('Host', ''))}; "
            f"object-src 'none'; base-uri 'self'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _proxy(self) -> bool:
        if not BFF or not self.path.startswith(("/v1", "/apps", "/agent")):
            return False
        length = int(self.headers.get("content-length", "0"))
        request = Request(
            f"{BFF}{self.path}",
            data=self.rfile.read(length) if length else None,
            headers={
                key: value
                for key, value in self.headers.items()
                if key.lower() not in {"host", "content-length", "connection"}
            },
            method=self.command,
        )
        try:
            response = urlopen(request, timeout=30)
        except HTTPError as error:
            response = error
        except URLError:
            self.send_error(502, "portal BFF unavailable")
            return True
        body = response.read()
        self.send_response(response.status)
        for key, value in response.headers.items():
            if key.lower() not in {"content-length", "connection", "transfer-encoding"}:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_POST(self) -> None:
        if not self._proxy():
            self.send_error(405)

    def do_GET(self) -> None:
        if STUB_EMBEDS and self.path.startswith(("/apps/", "/agent")):
            body = b"<!doctype html><title>Synthetic embedded app</title><p>ready</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self._proxy():
            return
        if self.path == "/healthz":
            body = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not (ROOT / self.path.lstrip("/")).exists():
            self.path = "/index.html"
        super().do_GET()


if __name__ == "__main__":
    os.chdir(ROOT)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
