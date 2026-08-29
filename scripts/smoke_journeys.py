#!/usr/bin/env python3
"""Prove the live journey composition through the portal origin only.

Run this after ``scripts/run_journeys.py`` has declared the stack ready.  The smoke discovers
the mounted apps from the portal's journey feed, checks each proxied health endpoint reports an
offline ``local`` or a real ``live`` profile (nothing else passes), then uses
the real Human Review Console API to prove portal-selected identity injection.  It intentionally
sends a conflicting browser persona header on that call; the returned review maker must
still be the principal selected by the portal cookie.

The smoke then withdraws the item it raised, under a second, independent persona, so the review
queue is left exactly as it was found.  The demonstration presents that queue and says out loud
what is in it, so a smoke run must not add a row to it.

The feed is the whole static catalog, so after ``run_journeys.py --journey <key>`` (which starts
one journey's apps only) pass the same ``--journey <key>`` here.  Without it the smoke would walk
apps that were never launched and fail on their proxied health checks.

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

# The persona that raises the smoke's review item, and the independent one that withdraws it
# again. They have to differ: the console refuses a self-approval, so an item raised by the
# only persona holding the approver entitlement could never be disposed of, and every smoke run
# left a permanent smoke row in the very queue the demonstration presents. Because they differ,
# the withdrawal below is also a real four-eyes disposition rather than only a tidy-up.
_MAKER_PERSONA_ID = "analyst"
_CHECKER_PERSONA_ID = "approver"
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


def _select_journeys(feed: dict[str, Any], selected: tuple[str, ...]) -> dict[str, Any]:
    """The journey feed narrowed to the named keys (unchanged when none are named).

    The portal serves its whole static catalog whatever the launcher started, so a
    single-journey launch has to say which journey it launched.  A key the feed does not
    carry is a failure, not an empty selection: silently checking nothing would pass.
    """
    if not selected:
        return feed
    raw_journeys = feed.get("journeys")
    if not isinstance(raw_journeys, list):
        raise SmokeFailure("GET /v1/journeys: missing journeys list")
    kept = [
        journey
        for journey in raw_journeys
        if isinstance(journey, dict) and journey.get("key") in selected
    ]
    missing = sorted(set(selected) - {journey.get("key") for journey in kept})
    if missing:
        raise SmokeFailure(f"GET /v1/journeys: no journey named {', '.join(missing)}")
    return {"journeys": kept}


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


def _select_persona(client: JsonRequester, persona_id: str) -> str:
    """Select one demo persona at the portal and return the principal it verified."""
    selected = _expect_status(
        client.request_json("POST", "/v1/session/persona", payload={"id": persona_id}),
        200,
        "POST /v1/session/persona",
    )
    subject = selected.get("subject")
    if selected.get("persona") != persona_id or not isinstance(subject, str) or not subject:
        raise SmokeFailure(
            f"POST /v1/session/persona: portal did not select the principal {persona_id!r} "
            f"(received {selected.get('persona')!r})"
        )
    return subject


def _withdraw_smoke_review(client: JsonRequester, review: dict[str, Any]) -> None:
    """Reject the smoke's own item, so the queue is left exactly as the smoke found it.

    The console lists PENDING items, so a rejected item leaves the queue the demo shows. That
    matters because the run sheet tells the room the queue holds exactly the escalations the
    demonstration itself produced, and an undisposed smoke item made that untrue on every run.

    The withdrawal is also a second proof rather than only a tidy-up: it is accepted only
    because the portal injected a different verified principal on this call, and the console
    checked that principal against the item's maker.
    """
    review_id = review.get("review_id")
    if not isinstance(review_id, str) or not review_id:
        raise SmokeFailure("POST /apps/hrz7/api/v1/reviews: the created item carried no id")
    checker = _select_persona(client, _CHECKER_PERSONA_ID)
    path = f"/apps/hrz7/api/v1/reviews/{review_id}/decision"
    outcome = _expect_status(
        client.request_json(
            "POST",
            path,
            payload={
                "disposition": "reject",
                "reason": "Portal smoke item withdrawn by the smoke run that raised it.",
            },
        ),
        200,
        f"POST {path}",
    )
    item = outcome.get("item")
    state = item.get("state") if isinstance(item, dict) else None
    if outcome.get("decision") != "allowed" or state != "rejected":
        raise SmokeFailure(
            f"POST {path}: the smoke item was not withdrawn and would stay in the demo queue "
            f"(decision {outcome.get('decision')!r}, state {state!r}, "
            f"findings {outcome.get('findings')!r})"
        )
    print(f"PASS {path} withdrawn by {checker} (review queue left as it was found)")


def _prove_injected_identity(client: JsonRequester) -> None:
    subject = _select_persona(client, _MAKER_PERSONA_ID)

    review = _expect_status(
        client.request_json(
            "POST",
            "/apps/hrz7/api/v1/reviews",
            payload={
                "action": "journey_portal_smoke",
                "subject": f"smoke-{uuid.uuid4().hex[:12]}",
                "summary": "Fictional portal smoke-review item, withdrawn once it has proved it.",
                "severity": "low",
                "required_approvals": 1,
            },
            # This is a browser-spoofed identity. The portal must strip it and inject the
            # principal it verified for the selected persona instead.
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
    _withdraw_smoke_review(client, review)


def run_smoke(client: JsonRequester, selected: tuple[str, ...] = ()) -> None:
    """Run the end-to-end checks using only portal-relative paths.

    ``selected`` names the journeys that were launched; empty means the whole catalog.
    """
    feed = _expect_status(client.request_json("GET", "/v1/journeys"), 200, "GET /v1/journeys")
    apps = _mounted_apps(_select_journeys(feed, selected))
    _check_mounted_app_health(client, apps)
    if "hrz7" not in {app_id for app_id, _api_base in apps}:
        # The identity proof runs against the real Human Review Console, so it is only
        # available where that console is mounted. On a full run its absence is a broken
        # catalog; on a single-journey run it is simply a journey that does not embed it
        # (`rm`), and saying so is honest where claiming a pass would not be.
        if not selected:
            raise SmokeFailure("GET /v1/journeys: hrz7 is required for the real identity proof")
        print(
            f"SKIP identity proof: journey {', '.join(selected)} does not mount hrz7; "
            "run the smoke against a journey that does to prove injected identity"
        )
        return
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
    parser.add_argument(
        "--journey",
        action="append",
        metavar="KEY",
        dest="journeys",
        help=(
            "check only this journey's apps (repeatable); pass the same key the launcher "
            "was given. Default: every app in the portal's catalog."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.timeout <= 0:
        print("FAIL --timeout must be greater than zero", file=sys.stderr)
        return 2
    try:
        run_smoke(
            PortalClient(args.base_url, timeout=args.timeout),
            tuple(args.journeys or ()),
        )
    except (SmokeFailure, ValueError) as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("PASS journey portal smoke completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
