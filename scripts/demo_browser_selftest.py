#!/usr/bin/env python3
"""Headless anti-rot test for both production-built shells and the real local BFF."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def wait_ready(url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"service did not become ready: {url}")


def start(command: list[str], **updates: str) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env.update(updates)
    return subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    services = [
        start(
            [sys.executable, "-m", "uvicorn", "journey_portal.api.app:app", "--port", "8110"],
            PYTHONPATH=str(ROOT / "src"),
            PORTAL_PROFILE="local",
        ),
        start(
            [sys.executable, str(ROOT / "ui-rm" / "static_server.py")],
            PORT="3000",
            STATIC_ROOT=str(ROOT / "ui-rm" / "out"),
            PORTAL_BFF_ORIGIN="http://127.0.0.1:8110",
            PORTAL_SELFTEST_STUB_EMBEDS="1",
        ),
        start(
            [sys.executable, str(ROOT / "ui-ops" / "static_server.py")],
            PORT="4200",
            STATIC_ROOT=str(ROOT / "ui-ops" / "dist" / "ops-journey-shell" / "browser"),
            PORTAL_BFF_ORIGIN="http://127.0.0.1:8110",
            PORTAL_SELFTEST_STUB_EMBEDS="1",
        ),
    ]
    try:
        wait_ready("http://127.0.0.1:8110/healthz")
        wait_ready("http://127.0.0.1:3000/healthz")
        wait_ready("http://127.0.0.1:4200/healthz")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            browser_errors: list[str] = []
            page.on(
                "console",
                lambda message: (
                    browser_errors.append(message.text) if message.type == "error" else None
                ),
            )
            page.goto("http://localhost:3000", wait_until="domcontentloaded")
            page.locator('[data-demo="rm-journey"]').wait_for()
            assert page.locator('[data-demo^="app-tab-"]').count() == 3
            page.locator('[data-demo="app-tab-doc5"]').wait_for()
            rm_src = page.locator('[data-demo="embedded-app"]').get_attribute("src") or ""
            assert urlsplit(rm_src).path.startswith("/apps/doc1"), rm_src
            page.locator('[data-demo="persona-selector"]').select_option("approver")
            page.locator('[data-demo="verified-identity"]').filter(
                has_text="demo.approver@bank.example"
            ).wait_for()

            page.goto("http://localhost:4200", wait_until="domcontentloaded")
            page.locator('[data-demo="ops-journey"]').wait_for()
            page.wait_for_timeout(1000)
            if page.locator('[data-demo^="app-tab-"]').count() == 0:
                raise AssertionError(page.locator("body").inner_text())
            page.locator('[data-demo^="app-tab-"]').first.wait_for()
            assert page.locator('[data-demo^="app-tab-"]').count() == 4
            page.locator('[data-demo="verified-identity"]').filter(
                has_text="demo.approver@bank.example"
            ).wait_for()
            page.locator('[data-demo="app-tab-hrz7"]').click()
            page.wait_for_function(
                """() => document.querySelector('[data-demo="embedded-app"]')
                  ?.getAttribute('src')?.includes('/apps/hrz7')"""
            )
            ops_src = page.locator('[data-demo="embedded-app"]').get_attribute("src") or ""
            assert urlsplit(ops_src).path.startswith("/apps/hrz7"), ops_src
            assert not browser_errors, browser_errors
            browser.close()
    finally:
        for service in reversed(services):
            service.terminate()
        for service in reversed(services):
            try:
                service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                service.kill()
    print(
        "PASS browser demo self-test: seven-app state, CSP, selectors, routing and shared persona"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
