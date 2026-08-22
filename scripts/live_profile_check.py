#!/usr/bin/env python3
"""Credentialed smoke check for the deployed RM and Ops journeys."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

_APP_HEALTH_ROUTES: dict[str, tuple[tuple[str, str, frozenset[str]], ...]] = {
    "rm": (
        ("doc1", "/agent/api/healthz", frozenset({"live", "gcp", "platform"})),
        ("doc5", "/apps/doc5/api/healthz", frozenset({"local", "gcp", "platform"})),
        ("doc3", "/apps/doc3/api/healthz", frozenset({"live", "gcp", "platform"})),
    ),
    "ops": (
        ("doc2", "/apps/doc2/api/healthz", frozenset({"live", "gcp", "platform"})),
        ("doc4", "/apps/doc4/api/healthz", frozenset({"live", "gcp", "platform"})),
        ("rsk1", "/apps/rsk1/api/healthz", frozenset({"live", "gcp", "platform"})),
        ("hrz7", "/apps/hrz7/api/healthz", frozenset({"gcp", "platform"})),
    ),
}
_EXPECTED_UI_BASES = {
    "doc1": ("/apps/doc1/", "/agent"),
    "doc2": ("/apps/doc2/", "/apps/doc2"),
    "doc3": ("/apps/doc3/", "/apps/doc3"),
    "doc4": ("/apps/doc4/", "/apps/doc4"),
    "doc5": ("/apps/doc5/", "/apps/doc5"),
    "rsk1": ("/apps/rsk1/", "/apps/rsk1"),
    "hrz7": ("/apps/hrz7/", "/apps/hrz7"),
}
_EXPECTED_JOURNEY_APPS = {
    "rm": ("doc1", "doc5", "doc3"),
    "ops": ("doc2", "doc4", "rsk1", "hrz7"),
}


class LiveCheckError(RuntimeError):
    """The deployed journey did not satisfy the live-profile contract."""


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: bytes
    location: str = ""


class Requester(Protocol):
    def get(self, base_url: str, path: str, token: str) -> Response: ...


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: object,
        fp: object,
        code: object,
        msg: object,
        headers: object,
        newurl: object,
    ) -> None:
        return None


class HttpsRequester:
    """HTTPS-only requester with an IAP bearer token."""

    def __init__(self, timeout: float) -> None:
        self._timeout = timeout
        self._opener = build_opener(_RejectRedirects)

    def get(self, base_url: str, path: str, token: str) -> Response:
        headers = {"Accept": "application/json,text/html"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")), headers=headers)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:  # noqa: S310
                return Response(
                    status=response.status,
                    content_type=response.headers.get_content_type(),
                    body=response.read(),
                    location=response.headers.get("Location", ""),
                )
        except HTTPError as exc:
            return Response(
                status=exc.code,
                content_type=exc.headers.get_content_type(),
                body=exc.read(),
                location=exc.headers.get("Location", ""),
            )
        except (URLError, OSError) as exc:
            raise LiveCheckError(f"GET {path} failed: {exc}") from exc


def _origin(name: str, value: str) -> str:
    parsed = urlparse(value)
    lowered = value.lower()
    try:
        _port = parsed.port
    except ValueError as exc:
        raise LiveCheckError(f"{name} must be an HTTPS origin") from exc
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or "*" in parsed.netloc
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]+[a-z0-9]", parsed.hostname or "")
    ):
        raise LiveCheckError(f"{name} must be an HTTPS origin")
    if any(marker in lowered for marker in ("replace", "placeholder", ".example.test")):
        raise LiveCheckError(f"{name} is still a placeholder")
    return value.rstrip("/")


def _json(response: Response, context: str) -> dict[str, Any]:
    if response.status != 200:
        raise LiveCheckError(f"{context} returned HTTP {response.status}")
    try:
        result = json.loads(response.body)
    except json.JSONDecodeError as exc:
        raise LiveCheckError(f"{context} did not return JSON") from exc
    if not isinstance(result, dict):
        raise LiveCheckError(f"{context} did not return a JSON object")
    return result


class _AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        source = values.get("src")
        if tag == "script" and isinstance(source, str):
            self.references.add(source)
        reference = values.get("href")
        if tag == "link" and isinstance(reference, str):
            rel = set((values.get("rel") or "").lower().split())
            if rel & {"stylesheet", "modulepreload", "preload"}:
                self.references.add(reference)


def _verify_ui(
    requester: Requester,
    *,
    origin: str,
    token: str,
    app_id: str,
    feed_base: str,
) -> None:
    expected_feed_base, build_base = _EXPECTED_UI_BASES[app_id]
    if feed_base != expected_feed_base:
        raise LiveCheckError(f"{app_id} journey feed exposed an unexpected iframe route")
    iframe_response = requester.get(origin, feed_base, token)
    if build_base != feed_base.rstrip("/"):
        if iframe_response.status != 307 or iframe_response.location != f"{build_base}/":
            raise LiveCheckError(f"{app_id} compatibility iframe route is not canonical")
        ui_response = requester.get(origin, f"{build_base}/", token)
    else:
        ui_response = iframe_response
    if (
        ui_response.status != 200
        or ui_response.content_type != "text/html"
        or not ui_response.body.strip()
    ):
        raise LiveCheckError(f"{app_id} embedded UI route is not healthy HTML")
    parser = _AssetParser()
    try:
        parser.feed(ui_response.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LiveCheckError(f"{app_id} embedded UI returned malformed HTML") from exc
    if not parser.references:
        raise LiveCheckError(f"{app_id} embedded UI exposed no verifiable build assets")
    checked = 0
    for reference in sorted(parser.references):
        parsed = urlparse(reference)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            continue
        asset_path = urlparse(urljoin(f"{build_base}/", reference)).path
        if asset_path != build_base and not asset_path.startswith(f"{build_base}/"):
            raise LiveCheckError(f"{app_id} asset escaped its reviewed build base path")
        asset_response = requester.get(origin, asset_path, token)
        if asset_response.status != 200 or not asset_response.body:
            raise LiveCheckError(f"{app_id} build asset is not reachable at {asset_path}")
        checked += 1
    if checked == 0:
        raise LiveCheckError(f"{app_id} embedded UI exposed no same-origin build assets")
    print(f"PASS {app_id}: iframe UI and {checked} base-path build assets")


def run_check(
    requester: Requester,
    *,
    rm_url: str,
    ops_url: str,
    token: str,
    expected_region: str,
) -> None:
    """Check shell roots, managed profile, verified identity, and both journey feeds."""

    if not token or any(marker in token.lower() for marker in ("replace", "placeholder")):
        raise LiveCheckError("LIVE_IAP_ID_TOKEN is absent or still a placeholder")
    if not expected_region or "replace" in expected_region.lower():
        raise LiveCheckError("expected region is absent or still a placeholder")
    origins = {"rm": _origin("RM URL", rm_url), "ops": _origin("Ops URL", ops_url)}
    journey_ids: set[str] = set()
    journey_apps: dict[str, tuple[str, ...]] = {}
    app_ui_bases: dict[str, tuple[str, str]] = {}
    for journey_name, origin in origins.items():
        unauthenticated = requester.get(origin, "/", "")
        if unauthenticated.status not in {302, 401, 403}:
            raise LiveCheckError(
                f"{journey_name} allowed unauthenticated access with HTTP {unauthenticated.status}"
            )
        root = requester.get(origin, "/", token)
        if root.status != 200 or root.content_type != "text/html":
            raise LiveCheckError(f"{journey_name} shell root is not healthy HTML")
        health = _json(requester.get(origin, "/healthz", token), f"{journey_name} health")
        if health.get("status") != "ok" or health.get("profile") not in {"gcp", "platform"}:
            raise LiveCheckError(f"{journey_name} health is not using a managed profile")
        if health.get("region") != expected_region:
            raise LiveCheckError(f"{journey_name} health reported an unexpected region")
        whoami = _json(requester.get(origin, "/v1/whoami", token), f"{journey_name} identity")
        if not whoami.get("subject") or whoami.get("source") != "gcp-iap":
            raise LiveCheckError(f"{journey_name} did not verify an IAP principal")
        journeys = _json(
            requester.get(origin, "/v1/journeys", token),
            f"{journey_name} journey feed",
        )
        for journey in journeys.get("journeys", []):
            if isinstance(journey, dict) and isinstance(journey.get("key"), str):
                journey_ids.add(journey["key"])
                if journey["key"] != journey_name:
                    continue
                raw_apps = journey.get("apps")
                if not isinstance(raw_apps, list):
                    raise LiveCheckError(f"{journey_name} journey has no app membership")
                membership = tuple(
                    app["id"]
                    for app in raw_apps
                    if isinstance(app, dict) and isinstance(app.get("id"), str)
                )
                if len(membership) != len(raw_apps):
                    raise LiveCheckError(f"{journey_name} journey has malformed app membership")
                journey_apps[journey_name] = membership
                for app in journey.get("apps", []):
                    if (
                        isinstance(app, dict)
                        and isinstance(app.get("id"), str)
                        and isinstance(app.get("ui_base"), str)
                    ):
                        app_ui_bases[app["id"]] = (origin, app["ui_base"])
        for app_id, health_path, allowed_profiles in _APP_HEALTH_ROUTES[journey_name]:
            app_health = _json(
                requester.get(origin, health_path, token),
                f"{app_id} health",
            )
            if app_health.get("status") != "ok":
                raise LiveCheckError(f"{app_id} health did not report ok")
            if app_health.get("profile") not in allowed_profiles:
                expected = ", ".join(sorted(allowed_profiles))
                raise LiveCheckError(
                    f"{app_id} health profile must be one of {expected}, "
                    f"received {app_health.get('profile')!r}"
                )
            if app_health.get("region") != expected_region:
                raise LiveCheckError(f"{app_id} health reported an unexpected region")
            print(f"PASS {app_id}: {app_health['profile']} health through {journey_name}")
        print(
            f"PASS {journey_name}: shell, {health['profile']} health, "
            f"IAP identity, and journey feed"
        )
    if journey_ids != {"rm", "ops"}:
        raise LiveCheckError("the hosted feed must expose exactly the RM and Ops journeys")
    if journey_apps != _EXPECTED_JOURNEY_APPS:
        raise LiveCheckError(
            "journey membership must be exactly RM doc1/doc5/doc3 and Ops doc2/doc4/rsk1/hrz7"
        )
    if set(app_ui_bases) != set(_EXPECTED_UI_BASES):
        raise LiveCheckError("the hosted feed did not expose exactly all seven embedded UIs")
    for app_id, (origin, feed_base) in app_ui_bases.items():
        _verify_ui(
            requester,
            origin=origin,
            token=token,
            app_id=app_id,
            feed_base=feed_base,
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rm-url", required=True)
    parser.add_argument("--ops-url", required=True)
    parser.add_argument("--expected-region", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args(argv)


def _live_iap_token() -> str:
    """Read the optional workflow token in three states using only the Python stdlib.

    This file is deliberately run by the GCP proof before the repo is installed. UNSET means the
    check has no credential and its authenticated probes will fail closed; SET-EMPTY is an
    operator/configuration error rather than another spelling of absence; SET-NONEMPTY is used
    exactly after surrounding whitespace is removed.
    """
    raw = os.environ.get("LIVE_IAP_ID_TOKEN")
    if raw is None:
        return ""
    value = raw.strip()
    if not value:
        raise LiveCheckError(
            "LIVE_IAP_ID_TOKEN is set but empty; unset it when no credential is available, "
            "or provide the credentialed live-proof token"
        )
    return value


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.timeout <= 0:
            raise LiveCheckError("timeout must be greater than zero")
        run_check(
            HttpsRequester(args.timeout),
            rm_url=args.rm_url,
            ops_url=args.ops_url,
            token=_live_iap_token(),
            expected_region=args.expected_region,
        )
    except LiveCheckError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("PASS hosted Hrz9 live-profile check completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
