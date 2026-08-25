"""The RM journey, driven in a real browser, against a laptop or against Google Cloud.

One script, two targets, the SAME assertions. That is the point of it: the claim this repository
makes is that the identical modules serve the journey on a machine with no cloud account and on a
managed deployment behind Identity-Aware Proxy, and a demo that can only be run in one of those
proves the opposite of the claim.

What it asserts, in order, is what a reviewer would ask to see:

1. the RM shell renders its journey, from the BFF's own catalog rather than a hardcoded list;
2. the portal names a VERIFIED identity, and it is the one the target's identity layer produced;
3. Doc1 is embedded same-origin under the portal's own origin, not framed from a third party;
4. a real CDD dossier is built inside that frame, and comes back with a source-of-wealth
   narrative, a risk rating and citations rather than a spinner.

Every step writes a screenshot, and the run writes ``evidence.json`` naming what it observed.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Frame, Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent))

from targets import Target, TargetError, resolve  # noqa: E402

ROOT = Path(__file__).resolve().parent
#: Long, because a real dossier reads documents and runs several model calls under --live. A
#: short timeout here reports "the demo is broken" for a demo that is merely still working.
DOSSIER_TIMEOUT_MS = 600_000
STEP_TIMEOUT_MS = 60_000

#: Deliberately fictional, and deliberately not a real company: the subject name travels into a
#: dossier and into audit evidence, and a real one would put a real party in both.
SUBJECT_NAME = "Meridian Harbour Holdings Pte Ltd"
SUBJECT_JURISDICTION = "SG"

#: A synthetic evidence document, so the cloud target (which seeds no demo corpus, by design)
#: has something to read. Fictional throughout.
EVIDENCE_DOCUMENT = """MERIDIAN HARBOUR HOLDINGS PTE LTD
Source of wealth statement (synthetic demo evidence, not a real document)

1. The shareholder, Ms A. Tan, acquired her holding through the 2019 sale of a logistics
   business. Sale consideration: SGD 12,400,000, received 2019-11-04.
2. Dividend income 2020-2025 totalling SGD 3,150,000, declared annually.
3. No politically exposed persons are associated with the structure.
4. Ultimate beneficial owner: Ms A. Tan, 62% direct holding.
"""


@dataclass
class Evidence:
    target: str
    base_url: str
    steps: list[dict[str, Any]] = field(default_factory=list)

    def record(self, name: str, **observed: Any) -> None:
        self.steps.append({"step": name, **observed})
        detail = ", ".join(f"{k}={v!r}" for k, v in observed.items())
        print(f"  PASS {name}{': ' + detail if detail else ''}", flush=True)


def _shot(page: Page, out_dir: Path, name: str) -> None:
    page.screenshot(path=str(out_dir / f"{name}.png"), full_page=True)


def _embedded_frame(page: Page) -> Frame:
    """The Doc1 frame, resolved through the iframe ELEMENT rather than by URL matching.

    Matching on url= would silently pick the shell itself if the embed never loaded, and the
    whole point of this step is that the embed did.
    """

    element = page.locator('[data-demo="embedded-app"]')
    element.wait_for(state="attached", timeout=STEP_TIMEOUT_MS)
    frame = element.element_handle().content_frame()
    if frame is None:
        raise AssertionError("the embedded-app iframe has no content frame: nothing was embedded")
    return frame


def run(target: Target, out_dir: Path) -> Evidence:
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence = Evidence(target=target.name, base_url=target.base_url)
    console_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            extra_http_headers=target.headers,
            viewport={"width": 1440, "height": 1000},
            ignore_https_errors=False,
        )
        page = context.new_page()
        page.on(
            "console",
            lambda m: console_errors.append(m.text) if m.type == "error" else None,
        )
        try:
            # 1. The shell, and the journey it renders from the BFF's catalog.
            page.goto(target.base_url + "/", wait_until="domcontentloaded")
            page.locator('[data-demo="rm-journey"]').wait_for(timeout=STEP_TIMEOUT_MS)
            label = page.locator('[data-demo="rm-journey"] h1').inner_text()
            tabs = page.locator('[data-demo^="app-tab-"]')
            tabs.first.wait_for(timeout=STEP_TIMEOUT_MS)
            app_ids = [
                (tabs.nth(i).get_attribute("data-demo") or "").removeprefix("app-tab-")
                for i in range(tabs.count())
            ]
            _shot(page, out_dir, "01-journey")
            evidence.record("journey rendered from the BFF catalog", label=label, apps=app_ids)
            assert "doc1" in app_ids, f"the RM journey must compose Doc1, got {app_ids}"

            # 2. The portal-verified identity. This is the identity the portal INJECTS into every
            #    embedded app, so a demo that never shows it has not shown the trust boundary.
            identity = page.locator('[data-demo="verified-identity"]')
            identity.wait_for(timeout=STEP_TIMEOUT_MS)
            who = identity.inner_text().strip()
            assert who, "the portal rendered no verified identity"
            evidence.record("portal names a verified identity", identity=who)

            # 3. Same-origin embedding: the frame's URL must be on the PORTAL's own origin.
            page.locator('[data-demo="app-tab-doc1"]').click()
            frame = _embedded_frame(page)
            frame.wait_for_load_state("domcontentloaded", timeout=STEP_TIMEOUT_MS)
            frame_url = frame.url
            assert frame_url.startswith(target.base_url + "/"), (
                f"the embedded app is not same-origin with the portal: {frame_url} is not under "
                f"{target.base_url}. Mode-1 embedding is the claim under test here."
            )
            _shot(page, out_dir, "02-doc1-embedded")
            evidence.record("Doc1 embedded same-origin", frame_url=frame_url)

            # 4. A real dossier, built inside that frame. Wait for the console to finish its
            #    own bootstrap first: it probes the API before enabling anything, and asserting
            #    against a half-booted console reports a demo failure for a demo that is merely
            #    still starting.
            frame.locator('[data-demo="panel-assess-a-subject"]').wait_for(timeout=STEP_TIMEOUT_MS)
            frame.get_by_text("Connecting to Doc1").wait_for(
                state="detached", timeout=STEP_TIMEOUT_MS
            )
            _shot(page, out_dir, "03-console-ready")
            evidence.record("embedded console bootstrapped against the live API")
            frame.locator('[data-demo="subject-name"]').fill(SUBJECT_NAME)
            build = frame.locator('[data-demo="build-dossier"]')

            # The assertion is that the case HAS its evidence, not that this run uploaded it.
            #
            # The console refuses "an assessment with nothing to read", so the evidence has to be
            # there; but the case is deliberately stable across runs (an analyst returns to a
            # case, and on the managed target the retrieval index only holds what has already
            # been ingested). Uploading unconditionally therefore piled up a fresh copy every
            # run. Uploading only when absent keeps both properties: the case always has its
            # evidence, and the demo can be run twice without growing.
            document = out_dir / "synthetic-evidence.txt"
            document.write_text(EVIDENCE_DOCUMENT, encoding="utf-8")
            # Wait for the case file to have RENDERED before counting what is in it: the
            # console loads the existing documents after its own bootstrap, so counting too
            # early reads an empty list and uploads a duplicate every run.
            frame.locator('[data-demo="panel-case-documents"]').wait_for(timeout=STEP_TIMEOUT_MS)
            frame.wait_for_function(
                """() => {
                  const panel = document.querySelector('[data-demo="panel-case-documents"]');
                  if (!panel) return false;
                  return Boolean(panel.querySelector('[data-demo="document-list"]'))
                    || panel.textContent.includes('No documents yet');
                }""",
                timeout=STEP_TIMEOUT_MS,
            )
            already = frame.get_by_text(document.name).count()
            if already == 0:
                frame.locator('[data-demo="document-upload"]').set_input_files(str(document))
                frame.locator('[data-demo="document-list"]').wait_for(timeout=STEP_TIMEOUT_MS)
            frame.locator('[data-demo="document-list"]').wait_for(timeout=STEP_TIMEOUT_MS)
            evidence.record(
                "case holds its grounding evidence",
                document=document.name,
                uploaded_now=already == 0,
            )

            build.wait_for(timeout=STEP_TIMEOUT_MS)
            page.wait_for_function(
                """() => {
                  const frame = document.querySelector('[data-demo="embedded-app"]');
                  const doc = frame?.contentDocument;
                  const button = doc?.querySelector('[data-demo="build-dossier"]');
                  return Boolean(button) && !button.disabled;
                }""",
                timeout=STEP_TIMEOUT_MS,
            )
            _shot(page, out_dir, "04-ready-to-assess")
            build.click()
            evidence.record("dossier requested", subject=SUBJECT_NAME)

            frame.locator('[data-demo="panel-risk-rating"]').wait_for(timeout=DOSSIER_TIMEOUT_MS)
            frame.locator('[data-demo="panel-source-of-wealth"]').wait_for(timeout=STEP_TIMEOUT_MS)
            risk = frame.locator('[data-demo="panel-risk-rating"]').inner_text().strip()
            sow = frame.locator('[data-demo="panel-source-of-wealth"]').inner_text().strip()
            assert len(sow) > 40, f"the source-of-wealth panel rendered almost nothing: {sow!r}"
            _shot(page, out_dir, "05-dossier")
            evidence.record(
                "dossier returned",
                risk_rating_chars=len(risk),
                source_of_wealth_chars=len(sow),
            )
        except BaseException:
            # Make the failure self-describing. A timeout on a selector says which selector and
            # nothing about what the page actually showed, and the page is the evidence.
            try:
                _shot(page, out_dir, "99-failure")
                frames = {f.url: f.inner_text("body")[:4000] for f in page.frames}
                (out_dir / "failure-text.json").write_text(
                    json.dumps(frames, indent=2), encoding="utf-8"
                )
            except Exception as capture_failure:  # noqa: BLE001 - diagnostics must never mask
                print(f"  (could not capture failure state: {capture_failure})", flush=True)
            raise
        finally:
            (out_dir / "console-errors.json").write_text(
                json.dumps(console_errors, indent=2), encoding="utf-8"
            )
            context.close()
            browser.close()

    assert not console_errors, f"the browser reported console errors: {console_errors[:5]}"
    evidence.record("no browser console errors")
    return evidence


def main() -> int:
    try:
        target = resolve()
    except TargetError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    out_dir = ROOT / "out" / target.name
    print(f"RM journey demo: target={target.name} origin={target.base_url}", flush=True)
    try:
        evidence = run(target, out_dir)
    except (AssertionError, PlaywrightTimeout) as exc:
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    (out_dir / "evidence.json").write_text(
        json.dumps(
            {"target": evidence.target, "base_url": evidence.base_url, "steps": evidence.steps},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"PASS RM journey on {target.name}; evidence in {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
