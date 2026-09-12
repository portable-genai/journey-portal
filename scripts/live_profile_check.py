#!/usr/bin/env python3
"""Credentialed smoke check for the deployed persona journey shells.

Each shell is one journey on one hostname, so the run is told which ones the deployment
publishes -- ``--shell rm=https://...`` once per host -- rather than taking a fixed pair. The
tables below state what each journey must contain; they are hard-coded rather than read from
``config/journeys.yaml`` because this file is deliberately stdlib-only (it runs on a hosted
builder before the repository is installed, and the stdlib has no YAML reader). A journey with no
row here is refused rather than checked loosely: a shell nobody stated the contents of would pass
by checking nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

_APP_HEALTH_ROUTES: dict[str, tuple[tuple[str, str, frozenset[str]], ...]] = {
    "rm": (
        ("cdd-sow-research", "/agent/api/healthz", frozenset({"live", "gcp", "platform"})),
        (
            "loan-document-intelligence",
            "/apps/loan-document-intelligence/api/healthz",
            frozenset({"local", "gcp", "platform"}),
        ),
        ("cio-advisory", "/apps/cio-advisory/api/healthz", frozenset({"live", "gcp", "platform"})),
    ),
    # The Marketing journey, publishable since 2026-09-12. Its gate is the one marketing app the
    # stack can deploy; the rest of the journey's apps are in the catalog and not yet built as
    # deployable UI/API pairs, so naming them here would assert a shape no deployment has.
    "mkt": (
        (
            "marketing-compliance-gate",
            "/apps/marketing-compliance-gate/api/healthz",
            frozenset({"gcp", "platform"}),
        ),
        (
            "human-review-console",
            "/apps/human-review-console/api/healthz",
            frozenset({"gcp", "platform"}),
        ),
    ),
    "ops": (
        (
            "credit-memo-drafting",
            "/apps/credit-memo-drafting/api/healthz",
            frozenset({"live", "gcp", "platform"}),
        ),
        (
            "trade-finance-checker",
            "/apps/trade-finance-checker/api/healthz",
            frozenset({"live", "gcp", "platform"}),
        ),
        (
            "compliance-advisory",
            "/apps/compliance-advisory/api/healthz",
            frozenset({"live", "gcp", "platform"}),
        ),
        (
            "human-review-console",
            "/apps/human-review-console/api/healthz",
            frozenset({"gcp", "platform"}),
        ),
    ),
}
_EXPECTED_UI_BASES = {
    "cdd-sow-research": ("/apps/cdd-sow-research/", "/agent"),
    "credit-memo-drafting": ("/apps/credit-memo-drafting/", "/apps/credit-memo-drafting"),
    "cio-advisory": ("/apps/cio-advisory/", "/apps/cio-advisory"),
    "trade-finance-checker": ("/apps/trade-finance-checker/", "/apps/trade-finance-checker"),
    "loan-document-intelligence": (
        "/apps/loan-document-intelligence/",
        "/apps/loan-document-intelligence",
    ),
    "compliance-advisory": ("/apps/compliance-advisory/", "/apps/compliance-advisory"),
    "human-review-console": ("/apps/human-review-console/", "/apps/human-review-console"),
    "marketing-compliance-gate": (
        "/apps/marketing-compliance-gate/",
        "/apps/marketing-compliance-gate",
    ),
}
_EXPECTED_JOURNEY_APPS = {
    "rm": ("cdd-sow-research", "loan-document-intelligence", "cio-advisory"),
    "ops": (
        "credit-memo-drafting",
        "trade-finance-checker",
        "compliance-advisory",
        "human-review-console",
    ),
    "mkt": ("marketing-compliance-gate", "human-review-console"),
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


def parse_shell(raw: str) -> tuple[str, str]:
    """Read one ``--shell journey=origin`` pair, refusing a journey this file cannot state."""

    journey, separator, url = raw.partition("=")
    if not separator or not journey or not url:
        raise LiveCheckError(f"--shell must be journey=https://host, got {raw!r}")
    if journey not in _EXPECTED_JOURNEY_APPS or journey not in _APP_HEALTH_ROUTES:
        stated = ", ".join(sorted(_EXPECTED_JOURNEY_APPS))
        raise LiveCheckError(
            f"--shell names journey {journey!r}, whose contents this check does not state. "
            f"It states {stated}. Add its row to _EXPECTED_JOURNEY_APPS and _APP_HEALTH_ROUTES "
            "rather than checking a shell against nothing."
        )
    # The ORIGIN is validated in run_check, not here, so every caller -- the CLI and the tests
    # that drive run_check directly -- gets the same placeholder and scheme refusals.
    return journey, url


def run_check(
    requester: Requester,
    *,
    shells: Sequence[tuple[str, str]],
    token: str,
    expected_region: str,
) -> None:
    """Check every named shell's root, managed profile, verified identity and journey feed."""

    if not token or any(marker in token.lower() for marker in ("replace", "placeholder")):
        raise LiveCheckError("LIVE_IAP_ID_TOKEN is absent or still a placeholder")
    if not expected_region or "replace" in expected_region.lower():
        raise LiveCheckError("expected region is absent or still a placeholder")
    if not shells:
        raise LiveCheckError("name at least one shell with --shell journey=https://host")
    origins = {journey: _origin(f"{journey} URL", url) for journey, url in shells}
    if len(origins) != len(shells):
        raise LiveCheckError("--shell names a journey twice; one shell serves one journey")
    if len(set(origins.values())) != len(origins):
        raise LiveCheckError("--shell names one hostname twice; each shell owns its root path")
    unstated = sorted(set(origins) - set(_EXPECTED_JOURNEY_APPS))
    if unstated:
        raise LiveCheckError(
            f"this check does not state the contents of {', '.join(unstated)}; it states "
            f"{', '.join(sorted(_EXPECTED_JOURNEY_APPS))}"
        )
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
    # The feed must expose exactly the journeys this deployment was said to publish. A journey in
    # the feed that no shell serves is unreachable; a shell whose journey the feed has dropped is
    # the silent-fallback hazard -- that host renders a DIFFERENT persona's journey and looks well.
    if journey_ids != set(origins):
        raise LiveCheckError(
            "the hosted feed exposes "
            f"{', '.join(sorted(journey_ids))}, but the shells named are "
            f"{', '.join(sorted(origins))}"
        )
    expected_apps = {journey: _EXPECTED_JOURNEY_APPS[journey] for journey in origins}
    if journey_apps != expected_apps:
        raise LiveCheckError(
            "journey membership must be exactly "
            + "; ".join(
                f"{journey} {'/'.join(apps)}" for journey, apps in sorted(expected_apps.items())
            )
            + f", received {journey_apps!r}"
        )
    expected_uis = {app_id for apps in expected_apps.values() for app_id in apps}
    if set(app_ui_bases) != expected_uis:
        raise LiveCheckError(
            f"the hosted feed exposed {sorted(app_ui_bases)}, not the "
            f"{len(expected_uis)} embedded UIs these shells compose: {sorted(expected_uis)}"
        )
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
    parser.add_argument(
        "--shell",
        action="append",
        default=[],
        metavar="JOURNEY=URL",
        required=True,
        help=(
            "one persona shell, repeatable: --shell rm=https://rm.example --shell "
            "mkt=https://mkt.example. The deployment names the shells it publishes."
        ),
    )
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
            shells=[parse_shell(raw) for raw in args.shell],
            token=_live_iap_token(),
            expected_region=args.expected_region,
        )
    except LiveCheckError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("PASS hosted journey-portal live-profile check completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
