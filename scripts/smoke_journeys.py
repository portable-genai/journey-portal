#!/usr/bin/env python3
"""Prove the live journey composition through the portal origin only.

Run this after ``scripts/run_journeys.py`` has declared the stack ready.  The smoke discovers
the mounted apps from the portal's journey feed, checks each proxied health endpoint reports an
offline ``local`` or a real ``live`` profile (nothing else passes), then uses
the real Human Review Console API to prove portal-selected identity injection.  It intentionally
sends a conflicting browser persona header on that final call; the returned review maker must
still be the principal selected by the portal cookie.

The script uses only the Python standard library so it remains a convenient live-demo check and
does not add a runtime dependency to the portal service.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

_PERSONA_ID = "approver"
_SPOOFED_PERSONA_ID = "other-tenant"
# The profiles an embedded app may report through the portal: ``local`` is the offline demo
# stack, ``live`` is a real run (Doc1 under ``run_journeys.py --live``). Anything else, such as a
# cloud profile reached by accident, still fails the smoke.
_ALLOWED_PROFILES = ("local", "live")


class SmokeFailure(RuntimeError):
    """A live-demo assertion failed."""


@dataclass(frozen=True, slots=True)
class JsonResponse:
    status: int
    body: Any


class JsonRequester(Protocol):
    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonResponse: ...


class PortalClient:
    """Cookie-preserving JSON client whose URLs are always rooted at the portal origin."""

    def __init__(self, base_url: str, *, timeout: float) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("--base-url must be an absolute http(s) URL")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError(
                "--base-url must be an origin without a path, query string, or fragment"
            )
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout = timeout
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> JsonResponse:
        if not path.startswith("/"):
            raise ValueError("portal request paths must start with '/'")
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(
            urljoin(self._base_url, path.lstrip("/")),
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:  # noqa: S310
                status = response.status
                raw_body = response.read()
        except HTTPError as exc:
            status = exc.code
            raw_body = exc.read()
        except URLError as exc:
            raise SmokeFailure(f"{method} {path}: portal unavailable ({exc.reason})") from exc
        except OSError as exc:
            raise SmokeFailure(f"{method} {path}: portal request failed ({exc})") from exc
        try:
            decoded = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise SmokeFailure(
                f"{method} {path}: expected JSON response, received HTTP {status}"
            ) from exc
        return JsonResponse(status=status, body=decoded)


def _expect_status(response: JsonResponse, expected: int, context: str) -> dict[str, Any]:
    if response.status != expected:
        raise SmokeFailure(
            f"{context}: expected HTTP {expected}, received HTTP {response.status}: {response.body}"
        )
    if not isinstance(response.body, dict):
        raise SmokeFailure(
            f"{context}: expected a JSON object, received {type(response.body).__name__}"
        )
    return response.body


def _mounted_apps(journeys: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    raw_journeys = journeys.get("journeys")
    if not isinstance(raw_journeys, list):
        raise SmokeFailure("GET /v1/journeys: missing journeys list")
    apps: list[tuple[str, str]] = []
    for journey in raw_journeys:
        if not isinstance(journey, dict) or not isinstance(journey.get("apps"), list):
            raise SmokeFailure("GET /v1/journeys: malformed journey entry")
        for app in journey["apps"]:
            app_id = app.get("id") if isinstance(app, dict) else None
            api_base = app.get("api_base") if isinstance(app, dict) else None
            if (
                not isinstance(app_id, str)
                or not app_id
                or not isinstance(api_base, str)
                or not api_base.startswith("/")
                or "?" in api_base
                or "#" in api_base
            ):
                raise SmokeFailure("GET /v1/journeys: malformed app id or API base")
            entry = (app_id, api_base.rstrip("/"))
            if entry not in apps:
                apps.append(entry)
    if not apps:
        raise SmokeFailure("GET /v1/journeys: no mounted apps")
    return tuple(apps)


def _check_mounted_app_health(client: JsonRequester, apps: tuple[tuple[str, str], ...]) -> None:
    for _app_id, api_base in apps:
        path = f"{api_base}/healthz"
        health = _expect_status(client.request_json("GET", path), 200, f"GET {path}")
        profile = health.get("profile")
        if profile not in _ALLOWED_PROFILES:
            expected = " or ".join(repr(allowed) for allowed in _ALLOWED_PROFILES)
            raise SmokeFailure(f"GET {path}: expected profile {expected}, received {profile!r}")
        print(f"PASS {path} profile={profile}")


def _prove_injected_identity(client: JsonRequester) -> None:
    selected = _expect_status(
        client.request_json("POST", "/v1/session/persona", payload={"id": _PERSONA_ID}),
        200,
        "POST /v1/session/persona",
    )
    subject = selected.get("subject")
    if selected.get("persona") != _PERSONA_ID or not isinstance(subject, str) or not subject:
        raise SmokeFailure("POST /v1/session/persona: portal did not return the selected principal")

    review = _expect_status(
        client.request_json(
            "POST",
            "/apps/hrz7/api/v1/reviews",
            payload={
                "action": "journey_portal_smoke",
                "subject": f"smoke-{uuid.uuid4().hex[:12]}",
                "summary": "Fictional portal smoke-review item.",
                "severity": "low",
                "required_approvals": 1,
            },
            # This is a browser-spoofed identity. The portal must strip it and inject approver.
            headers={"X-Dev-Persona": _SPOOFED_PERSONA_ID},
        ),
        201,
        "POST /apps/hrz7/api/v1/reviews",
    )
    if review.get("maker") != subject:
        raise SmokeFailure(
            "POST /apps/hrz7/api/v1/reviews: injected identity proof failed "
            f"(expected maker {subject!r}, received {review.get('maker')!r})"
        )
    print(
        "PASS /apps/hrz7/api/v1/reviews "
        f"maker={subject} (spoofed persona {_SPOOFED_PERSONA_ID!r} was not accepted)"
    )


def run_smoke(client: JsonRequester) -> None:
    """Run the end-to-end checks using only portal-relative paths."""
    journeys = _expect_status(client.request_json("GET", "/v1/journeys"), 200, "GET /v1/journeys")
    apps = _mounted_apps(journeys)
    _check_mounted_app_health(client, apps)
    if "hrz7" not in {app_id for app_id, _api_base in apps}:
        raise SmokeFailure("GET /v1/journeys: hrz7 is required for the real identity proof")
    _prove_injected_identity(client)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8110",
        help="portal BFF origin (default: http://127.0.0.1:8110)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="per-request timeout in seconds (default: 10)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.timeout <= 0:
        print("FAIL --timeout must be greater than zero", file=sys.stderr)
        return 2
    try:
        run_smoke(PortalClient(args.base_url, timeout=args.timeout))
    except (SmokeFailure, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("PASS journey portal smoke completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
