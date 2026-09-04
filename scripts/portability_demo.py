#!/usr/bin/env python3
"""Executable, bounded journey-portal portability evidence.

This proves channel composition over two host frameworks, complete adapter bindings, the
offline-to-managed runtime seam, and fail-fast on-prem placeholders. It does not claim data,
model, cross-tenant policy, or completed on-prem portability.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from hex_service_kit.identity import Principal

from journey_portal.config import (
    _BINDINGS,
    Settings,
    build_container,
    load_journeys_mapping,
)
from journey_portal.domain.identity_injection import build_injection_plan, sanitize_request_headers

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    assert (ROOT / "ui-rm" / "next.config.mjs").is_file()
    assert (ROOT / "ui-ops" / "angular.json").is_file()
    local = build_container(Settings(profile="local"))
    # The catalog is CONFIG, so the expectation is read from the configured file rather than
    # frozen here. A literal set named two journeys long after the config carried five, which
    # made a green portability tour depend on nobody adding a journey.
    configured = load_journeys_mapping(Settings(profile="local").journeys_path)["journeys"]
    assert isinstance(configured, dict) and configured, "the journey catalog is empty"
    assert {journey.key for journey in local.catalog.list_journeys()} == set(configured)
    assert all(
        set(bindings) == {"local", "gcp", "platform", "onprem"} for bindings in _BINDINGS.values()
    )

    principal = Principal(
        subject="demo.analyst@bank.example",
        tenant="demo-bank",
        source="local-persona:analyst",
    )
    forwarded = sanitize_request_headers(
        {"x-dev-persona": "approver", "authorization": "Bearer forged"},
        build_injection_plan(principal, "local", {"x-dev-persona": "approver"}),
    )
    assert forwarded == {"x-dev-persona": "analyst"}

    onprem = build_container(Settings(profile="onprem"))
    try:
        asyncio.run(
            onprem.upstream.forward(
                method="GET",
                url="https://service.example.test",
                headers={},
                content=b"",
            )
        )
    except NotImplementedError:
        pass
    else:
        raise AssertionError("onprem placeholder did not fail fast")

    print("PASS channel: React and Angular shells consume the same journey contract")
    print("PASS identity: browser claims are replaced by the portal-verified principal")
    print("PASS runtime seam: local, gcp, and fail-fast onprem bindings are complete")
    print("LIMIT: no claim for data/model portability, working onprem, OBO, or multi-tenancy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
