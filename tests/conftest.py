"""Shared fixtures: point the config at the repo, and inject a recording upstream (no network)."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Make the repo root importable so the eval scorers (``eval.run_eval``) and this conftest
# (``tests.conftest``) resolve regardless of the pytest invocation directory.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# Resolve the journeys config by absolute path so the suite is CWD-independent, and pin the local
# (offline, no-auth) profile before the app module reads the environment.
os.environ.setdefault("PORTAL_PROFILE", "local")
os.environ["PORTAL_JOURNEYS"] = str(_REPO_ROOT / "config" / "journeys.yaml")

from fastapi.testclient import TestClient  # noqa: E402

from journey_portal.api import app as app_module  # noqa: E402
from journey_portal.domain.models import UpstreamResponse  # noqa: E402

#: A loopback peer for every ``TestClient``. The app-object exposure guard refuses the
#: unauthenticated ``local`` posture to any other peer, and TestClient's DEFAULT peer is the
#: literal host ``"testclient"``, which is not a loopback address and is refused with a 503.
LOOPBACK_PEER = ("127.0.0.1", 50000)


class RecordingUpstream:
    """A fake UpstreamClientPort that records the forwarded call and returns a canned response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.response = UpstreamResponse(
            status=200,
            headers=(("content-type", "application/json"), ("set-cookie", "app=1")),
            body=b'{"ok": true}',
            media_type="application/json",
        )

    async def forward(
        self, *, method: str, url: str, headers: Mapping[str, str], content: bytes
    ) -> UpstreamResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "content": content}
        )
        return self.response

    async def aclose(self) -> None:
        return None

    @property
    def last(self) -> dict[str, object]:
        return self.calls[-1]


@pytest.fixture
def recording_upstream() -> RecordingUpstream:
    return RecordingUpstream()


@pytest.fixture(autouse=True)
def isolated_local_audit_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Give every test its own durable ledger so assertions cannot leak across cases."""
    monkeypatch.setenv("PORTAL_LOCAL_AUDIT_DB", str(tmp_path / "portal-access-audit.sqlite3"))


@pytest.fixture
def client(recording_upstream: RecordingUpstream) -> Iterator[TestClient]:
    app_module._container.cache_clear()
    app_module.app.dependency_overrides[app_module._upstream] = lambda: recording_upstream
    with TestClient(app_module.app, client=LOOPBACK_PEER) as test_client:
        yield test_client
    app_module.app.dependency_overrides.clear()
    app_module._container.cache_clear()
