#!/usr/bin/env python3
"""Prove every mounted app's console is ALIVE in a browser, not merely served.

The readiness table and `smoke_journeys.py` both answer server-side questions: is a process
listening, does its health endpoint reply, does the portal inject the identity it verified.
Neither can see the failure that actually broke this demo, because the failure happens in the
browser: a console whose client bundle never reaches its API renders its whole chrome, reports
its backend unreachable, and answers 200 to every check anyone was running.

So this asks the only question those checks cannot: **for each app in each journey, does the
console the audience will look at actually call its own API?** It opens the journey shell,
selects each app's tab, and waits for that app to issue a request to its own same-origin mount.
A console that never calls its API is reported by name, whatever its health endpoint says.

It is deliberately behavioural rather than visual: no per-app selectors, no fixtures, nothing to
keep in step with a form. Every app is held to one rule that cannot be satisfied by dead markup.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

# Journey key -> the shell port serving it. Mirrors run_journeys._SHELL_PORTS plus ops.
_SHELL_PORTS: dict[str, int] = {"rm": 3000, "ops": 4200, "mkt": 3001, "gov": 3002, "svc": 3003}
# How long one console gets to make its first call. A cold `next dev` route compiles on the
# first request, which on a laptop is seconds rather than milliseconds.
_APP_TIMEOUT_MS = 90_000


def _journeys(origin: str) -> list[dict[str, Any]]:
    import json

    with urlopen(f"{origin}/v1/journeys", timeout=15) as response:  # noqa: S310 - loopback only
        return list(json.load(response)["journeys"])


def _check_journey(browser: Any, journey: dict[str, Any], port: int) -> list[str]:
    """Return one failure line per app whose console never called its own API.

    One page for the whole journey, moving between tabs, because that is also what a person
    does. A fresh browser page per app spent nearly all of its time reloading the shell and
    made every console look slow when none of them was.
    """
    origin = f"http://localhost:{port}"
    failures: list[str] = []
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    # Every API call this page makes, in order. Each app is judged by whether one of them
    # names its own mount, which works for both mount shapes (`/apps/<id>/api` and
    # cdd-sow-research's
    # `/agent/api`) without the script knowing which is which.
    api_calls: list[str] = []
    page.on(
        "request",
        lambda request, seen=api_calls: (
            seen.append(request.url) if "/api/" in request.url else None
        ),
    )
    # A development server compiles with `eval`, and these consoles ship a policy that does
    # not permit it, so React never starts and the page is dead markup. One cause, a whole
    # class of "nothing works", and expensive to find from the symptom: name it here.
    eval_blocked: list[str] = []
    page.on(
        "console",
        lambda message, seen=eval_blocked: (
            seen.append(message.text)
            if message.type == "error" and "eval() is not supported" in message.text
            else None
        ),
    )

    try:
        page.goto(origin, wait_until="domcontentloaded", timeout=_APP_TIMEOUT_MS)
    except Exception as error:  # noqa: BLE001 - reported, never raised
        page.close()
        return [f"{journey['key']}: could not open the shell at {origin}: {error}"]

    try:
        for app in journey["apps"]:
            app_id, label, api_base = app["id"], app["label"], app["api_base"]
            try:
                page.get_by_role("button", name=label, exact=True).click(timeout=_APP_TIMEOUT_MS)
                # Wait for the shell to mount THIS app's frame, and no further. Waiting on the
                # frame's BODY to become visible looked equivalent and is not: a document that
                # has not painted yet has no visible body, so a slow console failed there as a
                # timeout rather than reaching the check this script exists to make.
                page.locator(f'iframe[title="{label}"]').wait_for(timeout=_APP_TIMEOUT_MS)
                waited = 0
                while not any(api_base in url for url in api_calls) and waited < _APP_TIMEOUT_MS:
                    page.wait_for_timeout(500)
                    waited += 500
            except Exception as error:  # noqa: BLE001 - every failure is reported, never raised
                failures.append(f"{journey['key']}/{app_id}: {type(error).__name__}: {error}")
                print(f"  FAIL {journey['key']}/{app_id:6} {type(error).__name__}")
                continue

            if any(api_base in url for url in api_calls):
                print(f"  PASS {journey['key']}/{app_id:6} console called {api_base}")
            elif eval_blocked:
                failures.append(
                    f"{journey['key']}/{app_id}: the console's own Content-Security-Policy "
                    "refused the `eval` a DEVELOPMENT server compiles with, so React never "
                    "started and the page is dead markup. This is the development server, not "
                    "the application: relaunch with `--built`, which is how the demo is meant "
                    "to run and how its captured evidence was produced."
                )
                print(f"  FAIL {journey['key']}/{app_id:6} dev-server eval blocked by its CSP")
            else:
                failures.append(
                    f"{journey['key']}/{app_id}: the console never called {api_base}. Its page "
                    "renders and its health endpoint answers, so this is the client bundle "
                    "failing to reach its API, not a stopped backend."
                )
                print(f"  FAIL {journey['key']}/{app_id:6} never called {api_base}")
    finally:
        page.close()
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--journey",
        action="append",
        choices=tuple(_SHELL_PORTS),
        help="check only this journey (repeatable); default is every journey with a shell up",
    )
    parser.add_argument("--bff", default="http://127.0.0.1:8110", help="portal BFF origin")
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        print("playwright is required: pip install playwright && playwright install chromium")
        return 2

    try:
        journeys = _journeys(args.bff)
    except (URLError, OSError) as error:
        print(f"cannot read {args.bff}/v1/journeys: {error}")
        return 2

    wanted = set(args.journey or _SHELL_PORTS)
    failures: list[str] = []
    checked = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for journey in journeys:
                key = journey["key"]
                if key not in wanted:
                    continue
                port = _SHELL_PORTS[key]
                try:  # a shell that is not up is skipped, not failed: --journey selects a subset
                    with urlopen(f"http://localhost:{port}/", timeout=5):  # noqa: S310
                        pass
                except (URLError, OSError):
                    print(f"SKIP {key}: no shell on :{port}")
                    continue
                print(f"{key} journey ({len(journey['apps'])} apps) via :{port}")
                failures.extend(_check_journey(browser, journey, port))
                checked += 1
        finally:
            browser.close()

    if not checked:
        print("no journey shell was reachable; nothing was proved")
        return 2
    if failures:
        print(f"\n{len(failures)} console(s) not alive:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nevery console in every checked journey called its own API")
    return 0


if __name__ == "__main__":
    sys.exit(main())
