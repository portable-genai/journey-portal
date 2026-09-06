#!/usr/bin/env python3
"""Offline audit-first demo: render the journeys and the identity trust boundary to static HTML.

Runs fully offline (domain only, no network, no cloud SDK). It shows, for a stakeholder:

* the two journeys and the apps each composes, with the same-origin mount paths the portal serves;
* three worked identity-injection cases proving the security invariant - a browser-asserted
  identity is stripped and the portal-verified identity is injected before a request reaches an
  embedded app.

Writes ``scripts/out/portal_demo.html`` (dependency-free, opens in any browser). Demo scripts live
outside the CI gate.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from hex_service_kit.identity import Principal

from journey_portal.config import Settings, load_journeys_mapping
from journey_portal.domain.audit import (
    GENESIS_HASH,
    PortalAuditService,
    audit_key_id,
    audit_reference,
)
from journey_portal.domain.catalog import JourneyCatalog
from journey_portal.domain.embed_policy import TenantEmbedPolicyService
from journey_portal.domain.identity_injection import (
    build_injection_plan,
    sanitize_request_headers,
)
from journey_portal.domain.models import (
    PortalAccessEvent,
    PortalAuditView,
    TenantEmbedAssessment,
    TenantEmbedPolicy,
)
from journey_portal.domain.observability_audit import to_observability_audit_event

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OUT = _REPO_ROOT / "scripts" / "out" / "portal_demo.html"
_JSON_OUT = _REPO_ROOT / "scripts" / "out" / "portal_audit_integrity.json"
_POLICY_JSON_OUT = _REPO_ROOT / "scripts" / "out" / "portal_embed_policy.json"
_OBSERVABILITY_JSON_OUT = _REPO_ROOT / "scripts" / "out" / "portal_observability_event.json"

_CASES = [
    {
        "title": "RM analyst (default persona), a browser tries to escalate",
        "profile": "local",
        "principal": Principal(
            subject="demo.analyst@bank.example",
            principals=("group:analyst", "group:risk"),
            tenant="demo-bank",
            source="local-persona:analyst",
        ),
        "inbound": {
            "x-dev-persona": "approver",
            "authorization": "Bearer forged-token",
            "accept-encoding": "gzip",
            "content-type": "application/json",
        },
    },
    {
        "title": "Ops auditor (selected in the portal), forged IAP header ignored",
        "profile": "local",
        "principal": Principal(
            subject="demo.auditor@bank.example",
            principals=("group:audit",),
            tenant="demo-bank",
            source="local-persona:auditor",
        ),
        "inbound": {
            "x-goog-iap-jwt-assertion": "FORGED-BY-BROWSER",
            "content-type": "application/json",
        },
    },
    {
        "title": "Cloud (IAP): edge assertion forwarded, browser persona stripped",
        "profile": "gcp",
        "principal": Principal(subject="rm@bank.example", tenant="bank", source="gcp-iap"),
        "inbound": {
            "x-goog-iap-jwt-assertion": "EDGE-SIGNED-ASSERTION",
            "x-dev-persona": "approver",
            "authorization": "Bearer forged-token",
        },
    },
]

_IDENTITY_KEYS = {"x-dev-persona", "x-goog-iap-jwt-assertion", "authorization"}


def _audit_view() -> PortalAuditView:
    service = PortalAuditService()
    key = b"fictional-demo-audit-key-32-bytes"
    key_id = audit_key_id(key)
    events = (
        PortalAccessEvent(
            event_id="fictional-access-001",
            occurred_at="2026-07-29T12:00:00+00:00",
            actor_ref=audit_reference(key, "actor", "fictional.rm@example.test"),
            tenant_ref=audit_reference(key, "tenant", "fictional-bank"),
            pseudonym_key_id=key_id,
            method="GET",
            action="forward:ui",
            app_id="cdd-sow-research",
        ),
        PortalAccessEvent(
            event_id="fictional-access-002",
            occurred_at="2026-07-29T12:01:00+00:00",
            actor_ref=audit_reference(key, "actor", "fictional.rm@example.test"),
            tenant_ref=audit_reference(key, "tenant", "fictional-bank"),
            pseudonym_key_id=key_id,
            method="POST",
            action="forward:api",
            app_id="cdd-sow-research",
        ),
    )
    records = []
    previous_hash = GENESIS_HASH
    for sequence, event in enumerate(events, start=1):
        record = service.build_record(
            sequence=sequence,
            event=event,
            previous_hash=previous_hash,
        )
        records.append(record)
        previous_hash = record.record_hash
    return service.verify(tuple(records))


def _policy_views() -> tuple[TenantEmbedAssessment, ...]:
    service = TenantEmbedPolicyService(
        (
            TenantEmbedPolicy(
                policy_id="fictional-bank-policy-v1",
                tenant="fictional-bank",
                hosts=("journey.fictional-bank.test",),
                frame_ancestors=("'self'", "https://host.fictional-bank.test"),
                cors_origins=("https://host.fictional-bank.test",),
            ),
        )
    )
    return (
        service.assess(
            request_host="journey.fictional-bank.test",
            tenant="fictional-bank",
            request_origin="https://host.fictional-bank.test",
        ),
        service.assess(
            request_host="journey.fictional-bank.test",
            tenant="other-bank",
        ),
    )


def _observability_view() -> dict[str, object]:
    return to_observability_audit_event(
        PortalAccessEvent(
            event_id="fictional-access-observability-001",
            occurred_at="2026-07-29T12:02:00+00:00",
            actor_ref="actor:v1:fictional-pseudonym",
            tenant_ref="tenant:v1:fictional-pseudonym",
            pseudonym_key_id="fictional-key-v1",
            method="POST",
            action="embed-policy:allowed",
            app_id="portal:fictional-bank-policy-v1",
        )
    )


def _headers_table(inbound: dict[str, str], forwarded: dict[str, str]) -> str:
    keys = sorted(set(inbound) | set(forwarded))
    rows = []
    for key in keys:
        before = inbound.get(key, "")
        after = forwarded.get(key, "")
        if before and not after:
            cls, note = "strip", "stripped"
        elif after and not before:
            cls, note = "inject", "injected by portal"
        elif before != after:
            cls, note = "inject", "replaced"
        else:
            cls, note = "keep", ""
        rows.append(
            f"<tr class='{cls}'><td>{html.escape(key)}</td>"
            f"<td>{html.escape(before) or '-'}</td>"
            f"<td>{html.escape(after) or '-'}</td>"
            f"<td class='note'>{note}</td></tr>"
        )
    return (
        "<table class='hdr'><thead><tr><th>header</th><th>from browser</th>"
        "<th>forwarded to app</th><th></th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _render(
    catalog: JourneyCatalog,
    audit_view: PortalAuditView,
    policy_views: tuple[TenantEmbedAssessment, ...],
    observability_view: dict[str, object],
) -> str:
    journeys_html = []
    for journey in catalog.list_journeys():
        apps = "".join(
            f"<li><b>{html.escape(m.label)}</b> "
            f"<code>{html.escape(m.mount_path)}/</code> "
            f"<span class='up'>ui &rarr; {html.escape(m.ui_upstream)} &middot; "
            f"api &rarr; {html.escape(m.api_upstream)}</span></li>"
            for m in catalog.apps_for(journey.key)
        )
        journeys_html.append(
            f"<div class='panel'><h3>{html.escape(journey.label)} "
            f"<span class='key'>{html.escape(journey.key)}</span></h3>"
            f"<p class='blurb'>{html.escape(journey.blurb)}</p><ul class='apps'>{apps}</ul></div>"
        )

    cases_html = []
    for case in _CASES:
        principal = case["principal"]
        assert isinstance(principal, Principal)
        profile = str(case["profile"])
        inbound = dict(case["inbound"])  # type: ignore[arg-type]
        plan = build_injection_plan(principal, profile, inbound)
        forwarded = sanitize_request_headers(inbound, plan)
        cases_html.append(
            f"<div class='panel'><h3>{html.escape(str(case['title']))}</h3>"
            f"<p class='meta'>profile <code>{html.escape(profile)}</code> &middot; "
            f"verified principal <code>{html.escape(principal.subject)}</code> "
            f"(tenant <code>{html.escape(principal.tenant)}</code>)</p>"
            f"{_headers_table(inbound, forwarded)}</div>"
        )

    status = "VERIFIED" if audit_view.valid else "INVESTIGATE"
    integrity = (
        "<div class='panel integrity'>"
        f"<h3>Local portal-access ledger <span class='pill'>{status}</span></h3>"
        f"<p class='meta'>{audit_view.record_count} content-free records linked by SHA-256</p>"
        f"<p>Head hash <code>{html.escape(audit_view.head_hash)}</code></p>"
        "<p class='meta'>Each actor and tenant is pseudonymized before append. Request bodies, "
        "queries, credentials and identity assertions are never stored.</p>"
        "</div>"
    )
    policy_panels = []
    for view in policy_views:
        finding_rows = "".join(
            f"<li><b>{html.escape(finding.kind.value)}</b>: "
            f"{html.escape(finding.summary)} "
            f"<span class='evidence'>{html.escape(finding.evidence_id)}</span></li>"
            for finding in view.findings
        )
        actions = "".join(f"<li>{html.escape(action)}</li>" for action in view.suggested_actions)
        findings_html = f"<ul class='findings'>{finding_rows}</ul>" if finding_rows else ""
        actions_html = f"<p class='meta'>Next actions</p><ul>{actions}</ul>" if actions else ""
        policy_panels.append(
            "<div class='panel policy'>"
            f"<h3>Tenant policy <code>{html.escape(view.policy_id or 'unresolved')}</code> "
            f"<span class='pill {view.decision}'>{view.decision.upper()}</span></h3>"
            f"<p class='meta'>host <code>{html.escape(view.request_host)}</code> &middot; "
            f"verified tenant <code>{html.escape(view.tenant)}</code> &middot; "
            f"origin <code>{html.escape(view.request_origin or 'same-origin')}</code></p>"
            f"<p>frame-ancestors <code>{html.escape(' '.join(view.frame_ancestors))}</code>"
            f" &middot; CORS {'allowed' if view.cors_allowed else 'not granted'}</p>"
            f"{findings_html}{actions_html}"
            "</div>"
        )
    return _TEMPLATE.format(
        journeys="".join(journeys_html),
        cases="".join(cases_html),
        policies="".join(policy_panels),
        observability=(
            "<div class='panel'><h3>agent-observability WORM handoff "
            "<span class='pill'>CONTENT-FREE</span></h3>"
            f"<p class='meta'>action <code>{html.escape(str(observability_view['action']))}</code> "
            f"&middot; decision <code>{html.escape(str(observability_view['decision']))}</code> "
            f"&middot; resource <code>{html.escape(str(observability_view['resource']))}</code></p>"
            "<p>Actor and tenant remain deployment-keyed pseudonyms. Prompt, response, "
            "citations, credentials and identity assertions are absent.</p></div>"
        ),
        integrity=integrity,
    )


_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Journey Portal - audit view</title>
<style>
 body{{font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
   margin:0;background:#0b1220;color:#e7edf7}}
 header{{padding:20px 28px;border-bottom:1px solid #22304d;background:#111a2e}}
 header h1{{margin:0 0 4px;font-size:18px}} header p{{margin:0;color:#9fb0cc}}
 main{{padding:20px 28px;display:grid;gap:16px;max-width:1000px}}
 h2{{font-size:13px;text-transform:uppercase;letter-spacing:1px;color:#9fb0cc;margin:12px 0 0}}
 .panel{{background:#111a2e;border:1px solid #22304d;border-radius:10px;padding:14px 16px}}
 .panel h3{{margin:0 0 6px;font-size:15px}}
 .key,.meta code,.up code{{color:#9fb0cc}}
 .key{{font-size:11px;border:1px solid #22304d;border-radius:5px;padding:1px 6px;margin-left:6px}}
 .blurb,.meta{{color:#9fb0cc;margin:0 0 8px}}
 ul.apps{{margin:0;padding-left:18px}} ul.apps li{{margin:3px 0}}
 code{{background:#0b1220;border:1px solid #22304d;border-radius:4px;padding:0 5px}}
 .up{{display:block;color:#7f90ac;font-size:12px;margin-left:2px}}
 table.hdr{{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}}
 table.hdr th{{text-align:left;color:#9fb0cc;font-weight:600;
   border-bottom:1px solid #22304d;padding:4px 8px}}
 table.hdr td{{padding:4px 8px;border-bottom:1px solid #182338;font-family:ui-monospace,monospace}}
 tr.strip td{{color:#ff9c9c}} tr.strip td:first-child{{text-decoration:line-through}}
 tr.inject td{{color:#8ff0c0}} .note{{font-family:inherit;color:#7f90ac;font-style:italic}}
 .pill{{font-size:11px;border-radius:999px;padding:2px 8px;margin-left:8px;
   background:#123d2a;color:#8ff0c0;border:1px solid #226c4a}}
 .pill.denied{{background:#411c24;color:#ff9c9c;border-color:#7a3041}}
 .findings{{color:#ffb3b3}} .evidence{{color:#9fb0cc;font-family:ui-monospace,monospace}}
 .integrity code{{word-break:break-all}}
</style></head><body>
<header><h1>Journey Portal Shell (journey-portal) - offline audit view</h1>
<p>One UI per persona, composed from the built P1 apps via same-origin embedding, with the
identity trust boundary shown per request.</p></header>
<main>
<h2>Journeys</h2>{journeys}
<h2>Identity trust boundary (browser &rarr; embedded app)</h2>{cases}
<h2>Tenant host, framing and CORS policy</h2>{policies}
<h2>Central observability handoff</h2>{observability}
<h2>Tamper-evident local access evidence</h2>{integrity}
</main></body></html>
"""


def main() -> int:
    catalog = JourneyCatalog.from_mapping(load_journeys_mapping(Settings.load().journeys_path))
    audit_view = _audit_view()
    policy_views = _policy_views()
    observability_view = _observability_view()
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(_render(catalog, audit_view, policy_views, observability_view))
    _JSON_OUT.write_text(json.dumps(audit_view.to_jsonable(), indent=2, sort_keys=True) + "\n")
    _POLICY_JSON_OUT.write_text(
        json.dumps(
            [view.to_jsonable() for view in policy_views],
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    _OBSERVABILITY_JSON_OUT.write_text(
        json.dumps(observability_view, indent=2, sort_keys=True) + "\n"
    )
    print(f"wrote {_OUT}")
    print(f"wrote {_JSON_OUT}")
    print(f"wrote {_POLICY_JSON_OUT}")
    print(f"wrote {_OBSERVABILITY_JSON_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
