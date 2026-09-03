"""Every app this installation serves, embedded and driven in a real browser.

``rm_journey.py`` proves one journey deeply: it builds a real CDD dossier and pairs the result
across profiles. This spec proves the OTHER half of the portal's claim, the wide one: that every
app the installation says it serves actually comes up embedded, same-origin, with its API reachable
through the portal's own proxy. Both targets, one spec, for the reason the deep one gives -- a
portal that composes on a laptop and not on the deployment has composed nothing.

What it asserts per app, all of which are properties of the PORTAL rather than of the app:

1. the shell renders the journey it was built for, with exactly the apps the BFF catalog declares
   (a shell whose journey has been dropped from the catalog silently renders a different one, and
   looks perfectly healthy doing it);
2. the app's UI is framed from the portal's OWN origin, under the mount path the catalog gives it;
3. the frame rendered a real document rather than a proxy error or an empty shell;
4. the app's own API call reached its backend THROUGH the portal (``<mount>/api/...``), which is
   the hop that makes an embedded app a composed app rather than a link.

What it does not do is press each app's buttons. Sixteen consoles have sixteen workflows, and a
sweep that tried to drive them all would be sixteen brittle specs wearing one name; the deep
journey is where a workflow is proved.

The set of apps is never hardcoded here. It comes from the target's own ``/v1/journeys``, which
is that installation's statement of what it serves -- so on a deployment mounting one app this
run drives one app and SAYS so, and the apps this checkout knows but the target does not serve
are reported as absent rather than quietly passing.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import APIRequestContext, BrowserContext, Frame, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent))

from targets import Shell, Target, TargetError, resolve, shells  # noqa: E402

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent

STEP_TIMEOUT_MS = 60_000
#: An embedded console probes its API on mount, so its first proxied call arrives with the frame.
#: Generous anyway: on a cold Cloud Run revision the first request pays a container start.
API_EVIDENCE_TIMEOUT_S = 45.0

#: How long the catalog fetch keeps retrying a 5xx. Sized for a Cloud Run cold start behind a
#: load balancer, which is the only thing it exists to absorb.
CATALOG_RETRY_S = 90.0

#: Below this, a frame is a spinner, a stack trace or a proxy error page rather than a console.
MIN_BODY_CHARS = 60

#: Narrow on purpose. Each is a page a browser or a framework renders INSTEAD of the app, never
#: prose an app would print about its own domain, so none of them can fire on a working console.
ERROR_MARKERS = (
    "This page could not be found",
    "Application error: a client-side exception has occurred",
    "Internal Server Error",
    "502 Bad Gateway",
    "504 Gateway Time-out",
    "Cannot GET ",
)


@dataclass
class AppResult:
    """One embedded app, as this run observed it."""

    app_id: str
    label: str
    mount_path: str
    frame_url: str = ""
    body_chars: int = 0
    proxied_api_calls: list[str] = field(default_factory=list)
    #: Requests this app made through the portal that came back 4xx/5xx. Recorded by URL rather
    #: than left to the console text, which says "Failed to load resource" and names nothing --
    #: a message that tells a reader an app is broken without telling them what broke.
    failed_requests: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    failure: str = ""

    @property
    def ok(self) -> bool:
        return not self.failure


@dataclass
class ShellResult:
    """One journey's shell, and every app it composes."""

    journey: str
    origin: str
    origin_source: str
    identity: str = ""
    catalog_apps: list[str] = field(default_factory=list)
    rendered_apps: list[str] = field(default_factory=list)
    apps: list[AppResult] = field(default_factory=list)
    failure: str = ""

    @property
    def ok(self) -> bool:
        return not self.failure and all(app.ok for app in self.apps)


def fetch_catalog(
    request: APIRequestContext, origin: str, attempts: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """The installation's own journey catalog: journey key -> its apps, from ``/v1/journeys``.

    Read through the SHELL's origin rather than from the config file beside this script, because
    what is under test is what the deployment serves, and a checkout can disagree with it.

    A 5xx on the FIRST request is retried, and only a 5xx. A deployment that scales to zero
    answers its first request after an idle period by starting a container, and the load balancer
    in front of it will give up before the container does: the first run of this spec against the
    rebuilt deployment took a 503 and the same URL answered 200 by hand seconds later. Retrying is
    not the same as ignoring, so every attempt is recorded in the report -- a cold start stays
    visible as a cold start instead of being smoothed into a clean run. A 4xx is never retried,
    because IAP refusing a token will refuse it just as firmly the second time.
    """

    deadline = time.monotonic() + CATALOG_RETRY_S
    while True:
        response = request.get(f"{origin}/v1/journeys", timeout=STEP_TIMEOUT_MS)
        attempts.append(f"GET {origin}/v1/journeys -> {response.status}")
        if response.ok:
            break
        retryable = response.status >= 500 and time.monotonic() < deadline
        if not retryable:
            raise AssertionError(
                f"GET {origin}/v1/journeys answered {response.status} after {len(attempts)} "
                "attempt(s): the portal's catalog is what every shell renders from, so nothing "
                "downstream of this is measurable."
            )
        print(
            f"  {response.status} from the catalog; retrying (a scale-to-zero revision answers "
            "its first request with a cold start)",
            flush=True,
        )
        time.sleep(5)
    body = response.json()
    journeys = body.get("journeys") if isinstance(body, dict) else None
    if not isinstance(journeys, list) or not journeys:
        raise AssertionError(f"{origin}/v1/journeys returned no journey: {body!r}")
    return {j["key"]: list(j["apps"]) for j in journeys}


def _embedded_frame(page: Page) -> Frame:
    """The embedded app's frame, resolved through the iframe ELEMENT rather than by URL.

    Matching on url= would silently pick the shell itself when nothing was embedded, which is the
    one outcome this spec exists to catch.
    """

    element = page.locator('[data-demo="embedded-app"]')
    element.wait_for(state="attached", timeout=STEP_TIMEOUT_MS)
    frame = element.element_handle().content_frame()
    if frame is None:
        raise AssertionError("the embedded-app iframe has no content frame: nothing was embedded")
    return frame


def _mounts_of(app: dict[str, Any]) -> tuple[str, ...]:
    """Every path this app is legitimately framed at, most specific first.

    Two, not one, and the difference is load-bearing. The shell frames an app at the portal's
    COMPATIBILITY mount (``/apps/<id>/``), but an app carrying a ``canonical_mount_path`` is
    redirected to it -- ``cdd-sow-research`` keeps ``/agent`` so its artifact holds one path
    across hosts. Asserting only the framed path calls that redirect a cross-origin escape.
    """

    compatibility = app["mount_path"].rstrip("/")
    canonical = app["api_base"].rstrip("/").removesuffix("/api")
    return (compatibility,) if canonical == compatibility else (canonical, compatibility)


def _under_mount(url: str, origin: str, mounts: tuple[str, ...]) -> bool:
    """Is ``url`` the portal's own origin, at or below one of this app's mounts?

    Trailing slashes are normalised on purpose: a shell frames ``/apps/<id>/`` and the browser
    reports ``/apps/<id>`` back, and a spec that called those two different origins would fail
    every app in the catalog while proving nothing about any of them.
    """

    for mount in mounts:
        base = f"{origin}{mount}"
        if url == base or url.startswith(f"{base}/") or url.startswith(f"{base}?"):
            return True
    return False


def _await_framed_app(page: Page, shell: Shell, app: dict[str, Any]) -> Frame:
    """Wait for the embed to be showing THIS app, then hand back its frame.

    Waiting, not asserting-immediately. The React shell keys the iframe by app id, so a tab click
    replaces the element; the Angular shell mutates one iframe's ``src`` in place. Read straight
    after the click, the second shell still reports the PREVIOUS app's URL, and every app but the
    first is then reported as framed from its predecessor -- a spec bug that reads exactly like a
    routing bug, which is the worst kind to publish.
    """

    mounts = _mounts_of(app)
    deadline = time.monotonic() + STEP_TIMEOUT_MS / 1000
    last = ""
    while True:
        frame = _embedded_frame(page)
        last = frame.url
        if _under_mount(last, shell.origin, mounts):
            frame.wait_for_load_state("domcontentloaded", timeout=STEP_TIMEOUT_MS)
            return frame
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"{app['id']} is framed from {last!r}, which is not under "
                f"{shell.origin}{mounts[0]}. Mode-1 same-origin embedding is the claim under "
                "test: a frame that is not on the portal's own origin, under this app's own "
                "mount, is a different product with different cookies, CSP and audit."
            )
        page.wait_for_timeout(250)


def _await_proxied_api_call(
    page: Page, responses: list[tuple[str, int]], origin: str, api_base: str
) -> list[str]:
    """Wait for the embedded app's own API call to come back THROUGH the portal proxy.

    Collected from the whole page rather than awaited around the click: the first app's frame is
    already loading when the shell renders, so a wait armed after the click would miss its calls
    and report the one app that needed no clicking as the one that never called home.

    The wait is ``page.wait_for_timeout`` and not ``time.sleep``, which is not a style choice.
    Playwright's sync API dispatches events only while the caller is inside a Playwright call, so
    a loop that sleeps on its own starves the very listener it is waiting on: the requests arrive,
    the list stays empty, and the run reports "the console never called its API" about a console
    that called it twice. Measured, not reasoned about -- it is what the first run of this spec
    said about ``credit-memo-drafting``, whose frame was meanwhile answering 200s.
    """

    prefix = f"{origin}{api_base}"
    deadline = time.monotonic() + API_EVIDENCE_TIMEOUT_S
    while True:
        seen = [url for url, status in responses if url.startswith(prefix) and status < 400]
        if seen:
            return seen
        if time.monotonic() >= deadline:
            failed = [f"{url} -> {status}" for url, status in responses if url.startswith(prefix)]
            raise AssertionError(
                f"no successful API call reached {prefix}/* within {API_EVIDENCE_TIMEOUT_S:.0f}s. "
                + (
                    f"The proxy answered: {failed[:3]}"
                    if failed
                    else "The console never called its API through the portal at all, so the "
                    "API hop of this mount is unproved."
                )
            )
        page.wait_for_timeout(250)


def drive_app(
    page: Page,
    shell: Shell,
    app: dict[str, Any],
    responses: list[tuple[str, int]],
    console_errors: list[str],
    out_dir: Path,
) -> AppResult:
    """Open one app's tab and assert the portal actually composed it."""

    result = AppResult(app_id=app["id"], label=app["label"], mount_path=app["mount_path"])
    errors_before = len(console_errors)
    responses_before = len(responses)
    try:
        page.locator(f'[data-demo="app-tab-{app["id"]}"]').click()
        frame = _await_framed_app(page, shell, app)
        result.frame_url = frame.url

        body = frame.locator("body").inner_text(timeout=STEP_TIMEOUT_MS).strip()
        result.body_chars = len(body)
        marker = next((m for m in ERROR_MARKERS if m in body), "")
        if marker:
            raise AssertionError(f"{app['id']} rendered an error page containing {marker!r}")
        if len(body) < MIN_BODY_CHARS:
            raise AssertionError(
                f"{app['id']} framed {len(body)} characters, which is a spinner or an empty "
                f"document rather than a console: {body[:120]!r}"
            )

        result.proxied_api_calls = _await_proxied_api_call(
            page, responses, shell.origin, app["api_base"]
        )
        page.screenshot(path=str(out_dir / f"{shell.journey}-{app['id']}.png"), full_page=True)
    except (AssertionError, PlaywrightTimeout) as exc:
        result.failure = f"{type(exc).__name__}: {exc}"
        try:
            page.screenshot(
                path=str(out_dir / f"{shell.journey}-{app['id']}-FAILED.png"), full_page=True
            )
        except Exception as capture_failure:  # noqa: BLE001 - diagnostics must never mask
            print(f"    (could not capture failure state: {capture_failure})", flush=True)
    result.console_errors = console_errors[errors_before:]
    result.failed_requests = [
        f"{url} -> {status}" for url, status in responses[responses_before:] if status >= 400
    ]
    return result


def drive_shell(
    context: BrowserContext,
    shell: Shell,
    catalog: dict[str, list[dict[str, Any]]],
    out_dir: Path,
) -> ShellResult:
    """Open one shell and drive every app its journey composes."""

    result = ShellResult(journey=shell.journey, origin=shell.origin, origin_source=shell.source)
    apps = catalog[shell.journey]
    result.catalog_apps = [a["id"] for a in apps]
    print(f"\n{shell.journey} shell: {shell.origin} ({shell.source})", flush=True)

    responses: list[tuple[str, int]] = []
    console_errors: list[str] = []
    page = context.new_page()
    page.on("response", lambda r: responses.append((r.url, r.status)))
    page.on(
        "console",
        lambda m: console_errors.append(m.text) if m.type == "error" else None,
    )
    try:
        page.goto(shell.origin + "/", wait_until="domcontentloaded")
        page.locator(f'[data-demo="{shell.journey}-journey"]').wait_for(timeout=STEP_TIMEOUT_MS)

        identity = page.locator('[data-demo="verified-identity"]')
        identity.wait_for(timeout=STEP_TIMEOUT_MS)
        result.identity = identity.inner_text().strip()
        if not result.identity:
            raise AssertionError("the shell rendered no verified identity")

        tabs = page.locator('[data-demo^="app-tab-"]')
        tabs.first.wait_for(timeout=STEP_TIMEOUT_MS)
        result.rendered_apps = [
            (tabs.nth(i).get_attribute("data-demo") or "").removeprefix("app-tab-")
            for i in range(tabs.count())
        ]
        # The shell falls back to the first journey in the catalog when its own is absent, and a
        # fallback renders a healthy page under the wrong journey's name. Comparing the TABS to
        # the catalog is what tells the two apart; the data-demo above only names the build.
        if set(result.rendered_apps) != set(result.catalog_apps):
            raise AssertionError(
                f"the {shell.journey} shell renders {result.rendered_apps}, but the catalog says "
                f"that journey is {result.catalog_apps}. A shell serving a journey it was not "
                "built for is the failure mode this comparison exists for."
            )
        print(f"  identity={result.identity!r} apps={result.rendered_apps}", flush=True)

        for app in apps:
            app_result = drive_app(page, shell, app, responses, console_errors, out_dir)
            result.apps.append(app_result)
            if app_result.ok:
                print(
                    f"  PASS {app_result.app_id}: framed {app_result.frame_url}, "
                    f"{app_result.body_chars} chars, "
                    f"{len(app_result.proxied_api_calls)} proxied API call(s)",
                    flush=True,
                )
            else:
                print(f"  FAIL {app_result.app_id}: {app_result.failure}", flush=True)
    except (AssertionError, PlaywrightTimeout) as exc:
        result.failure = f"{type(exc).__name__}: {exc}"
        print(f"  FAIL {shell.journey} shell: {result.failure}", flush=True)
        try:
            page.screenshot(path=str(out_dir / f"{shell.journey}-shell-FAILED.png"), full_page=True)
        except Exception as capture_failure:  # noqa: BLE001 - diagnostics must never mask
            print(f"    (could not capture failure state: {capture_failure})", flush=True)
    finally:
        page.close()
    return result


def _checkout_catalogue() -> set[str]:
    """Every app id THIS checkout's config knows how to serve.

    Only ever reported against, never asserted on: the checkout is not the authority on what a
    deployment mounts. It is what turns "one app passed" into "one of sixteen is deployed here".
    """

    sys.path.insert(0, str(REPO / "src"))
    from journey_portal.config import load_journeys_mapping  # noqa: PLC0415

    raw = load_journeys_mapping(REPO / "config" / "journeys.yaml")
    apps = raw.get("apps")
    return set(apps) if isinstance(apps, dict) else set()


def run(target: Target, out_dir: Path) -> tuple[list[ShellResult], tuple[str, ...], list[str]]:
    """Drive every shell this run has an origin for; return its results and what it never opened."""

    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[ShellResult] = []
    undriven: tuple[str, ...] = ()
    catalog_attempts: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            extra_http_headers=target.headers,
            viewport={"width": 1440, "height": 1000},
            ignore_https_errors=False,
        )
        try:
            catalog = fetch_catalog(context.request, target.base_url, catalog_attempts)
            drivable, undriven = shells(target, tuple(catalog))
            print(
                f"catalog: {len(catalog)} journey(s) "
                f"{sorted(catalog)}; driving {[s.journey for s in drivable]}",
                flush=True,
            )
            if undriven:
                print(
                    f"NOT DRIVEN: {sorted(undriven)} -- this target serves them and this run has "
                    "no origin for them. Name one with PORTAL_E2E_SHELL_<JOURNEY>_BASE_URL.",
                    flush=True,
                )
            for shell in drivable:
                results.append(drive_shell(context, shell, catalog, out_dir))
        finally:
            context.close()
            browser.close()
    return results, undriven, catalog_attempts


def _write_report(
    target: Target,
    results: list[ShellResult],
    undriven: tuple[str, ...],
    catalog_attempts: list[str],
    out_dir: Path,
) -> Path:
    served = {app.app_id for shell in results for app in shell.apps}
    catalogue = _checkout_catalogue()
    report = {
        "target": target.name,
        "base_url": target.base_url,
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        # Every attempt, so one 503 absorbed as a cold start is still on the record.
        "catalog_attempts": catalog_attempts,
        "shells": [asdict(shell) for shell in results],
        "apps_driven": sorted(served),
        "journeys_not_driven": sorted(undriven),
        # Named exactly for what it is: a difference between this checkout and that target,
        # which on a deployment mounting one app is the honest headline of the whole run.
        "apps_in_this_checkout_not_served_by_target": sorted(catalogue - served),
    }
    path = out_dir / "coverage.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    try:
        target = resolve()
    except TargetError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    out_dir = ROOT / "out" / target.name / "apps"
    print(f"App coverage: target={target.name} origin={target.base_url}", flush=True)

    try:
        results, undriven, catalog_attempts = run(target, out_dir)
    except TargetError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    except (AssertionError, PlaywrightTimeout) as exc:
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    report = _write_report(target, results, undriven, catalog_attempts, out_dir)

    apps = [app for shell in results for app in shell.apps]
    failed_apps = [app for app in apps if not app.ok]
    failed_shells = [shell for shell in results if shell.failure]
    noisy = [app for app in apps if app.console_errors]

    print(
        f"\n{len(apps) - len(failed_apps)}/{len(apps)} app(s) composed across "
        f"{len(results)} shell(s) on {target.name}; report in {report}",
        flush=True,
    )
    for app in noisy:
        detail = app.failed_requests[:3] or app.console_errors[:3]
        print(f"  BROKEN REQUEST in {app.app_id}: {detail}", flush=True)
    if failed_shells or failed_apps:
        for shell in failed_shells:
            print(f"FAIL {shell.journey} shell: {shell.failure}", file=sys.stderr)
        for app in failed_apps:
            print(f"FAIL {app.app_id}: {app.failure}", file=sys.stderr)
        return 1
    if noisy:
        print(
            "FAIL the browser reported console errors in an embedded app. Same standard as the "
            "deep journey, which has asserted a clean console since it was written.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
