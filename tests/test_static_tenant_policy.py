"""Both production static shells apply the same host-bound tenant framing contract."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


def _server(path: str, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, Path(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("path", "module_name"),
    [
        ("ui-rm/static_server.py", "rm_static_server_test"),
        ("ui-ops/static_server.py", "ops_static_server_test"),
    ],
)
def test_static_shell_resolves_exact_host_policy(
    path: str,
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(path, module_name)
    monkeypatch.setenv(
        "TENANT_EMBED_POLICIES_JSON",
        json.dumps(
            {
                "fictional-bank-v1": {
                    "tenant": "fictional-bank",
                    "hosts": ["journey.fictional-bank.test"],
                    "frame_ancestors": ["'self'", "https://host.fictional-bank.test"],
                    "cors_origins": [],
                }
            }
        ),
    )

    assert server.frame_ancestors("journey.fictional-bank.test:443") == (
        "'self' https://host.fictional-bank.test"
    )
    assert server.frame_ancestors("unknown.test") == "'none'"


@pytest.mark.parametrize(
    ("path", "module_name"),
    [
        ("ui-rm/static_server.py", "rm_static_server_malformed_test"),
        ("ui-ops/static_server.py", "ops_static_server_malformed_test"),
    ],
)
def test_static_shell_malformed_policy_fails_closed(
    path: str,
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(path, module_name)
    monkeypatch.setenv("TENANT_EMBED_POLICIES_JSON", "{not-json")

    assert server.frame_ancestors("journey.fictional-bank.test") == "'none'"


@pytest.mark.parametrize(
    ("path", "module_name"),
    [
        ("ui-rm/static_server.py", "rm_static_server_unsafe_ancestor_test"),
        ("ui-ops/static_server.py", "ops_static_server_unsafe_ancestor_test"),
    ],
)
def test_static_shell_unsafe_frame_ancestor_fails_closed(
    path: str,
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(path, module_name)
    monkeypatch.setenv(
        "TENANT_EMBED_POLICIES_JSON",
        json.dumps(
            {
                "fictional-bank-v1": {
                    "tenant": "fictional-bank",
                    "hosts": ["journey.fictional-bank.test"],
                    "frame_ancestors": ["https://host.fictional-bank.test/path"],
                    "cors_origins": [],
                }
            }
        ),
    )

    assert server.frame_ancestors("journey.fictional-bank.test") == "'none'"


@pytest.mark.parametrize(
    ("path", "module_name", "origin"),
    [
        (
            "ui-rm/static_server.py",
            "rm_static_server_invalid_host_test",
            "https://invalid_host.test",
        ),
        (
            "ui-ops/static_server.py",
            "ops_static_server_invalid_host_test",
            "https://invalid_host.test",
        ),
        (
            "ui-rm/static_server.py",
            "rm_static_server_consecutive_dot_test",
            "https://a..fictional-bank.test",
        ),
        (
            "ui-ops/static_server.py",
            "ops_static_server_consecutive_dot_test",
            "https://a..fictional-bank.test",
        ),
        (
            "ui-rm/static_server.py",
            "rm_static_server_explicit_port_test",
            "https://host.fictional-bank.test:443",
        ),
        (
            "ui-ops/static_server.py",
            "ops_static_server_explicit_port_test",
            "https://host.fictional-bank.test:443",
        ),
        (
            "ui-rm/static_server.py",
            "rm_static_server_uppercase_test",
            "https://HOST.fictional-bank.test",
        ),
        (
            "ui-ops/static_server.py",
            "ops_static_server_uppercase_test",
            "https://HOST.fictional-bank.test",
        ),
    ],
)
def test_static_shell_invalid_origin_host_fails_closed(
    path: str,
    module_name: str,
    origin: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server(path, module_name)
    monkeypatch.setenv(
        "TENANT_EMBED_POLICIES_JSON",
        json.dumps(
            {
                "fictional-bank-v1": {
                    "tenant": "fictional-bank",
                    "hosts": ["journey.fictional-bank.test"],
                    "frame_ancestors": [origin],
                    "cors_origins": [],
                }
            }
        ),
    )

    assert server.frame_ancestors("journey.fictional-bank.test") == "'none'"


# --------------------------------------------------------------------------- #
# Three-state frame-ancestors on every surface that emits the directive
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("path", "module_name"),
    [
        ("ui-rm/static_server.py", "rm_static_server_three_state_test"),
        ("ui-ops/static_server.py", "ops_static_server_three_state_test"),
    ],
)
def test_static_shell_resolves_frame_ancestors_in_three_states(
    path: str,
    module_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset keeps the default; SET and naming no origin refuses; a real value is used.

    An empty CSP directive (``frame-ancestors `` with nothing after it) is a parse error
    browsers discard, and it also skips the ``'self'`` branch that adds ``X-Frame-Options``,
    so the clickjacking control would vanish from both channels at once. This shell already
    refuses rather than emitting one; the refusal is pinned here so it cannot regress into
    the two-state read the rest of the fleet had.
    """
    server = _server(path, module_name)
    monkeypatch.delenv("TENANT_EMBED_POLICIES_JSON", raising=False)

    monkeypatch.delenv("FRAME_ANCESTORS", raising=False)
    assert server.frame_ancestors("journey.fictional-bank.test") == "'self'"

    monkeypatch.setenv("FRAME_ANCESTORS", "https://host.fictional-bank.test")
    assert server.frame_ancestors("journey.fictional-bank.test") == (
        "https://host.fictional-bank.test"
    )

    for emptied in ("", "   "):
        monkeypatch.setenv("FRAME_ANCESTORS", emptied)
        with pytest.raises(ValueError, match="FRAME_ANCESTORS"):
            server.frame_ancestors("journey.fictional-bank.test")


@pytest.mark.parametrize(
    ("path", "module_name"),
    [
        ("ui-rm/static_server.py", "rm_static_server_script_hash_test"),
        ("ui-ops/static_server.py", "ops_static_server_script_hash_test"),
    ],
)
def test_static_shell_allows_only_exact_inline_build_scripts(
    path: str, module_name: str, tmp_path: Path
) -> None:
    server = _server(path, module_name)
    script = "self.__next_f.push(['reviewed-build']);"
    (tmp_path / "index.html").write_text(
        f'<script src="/main.js"></script><script>{script}</script>', encoding="utf-8"
    )
    server.ROOT = tmp_path

    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    sources = server.script_sources()
    assert "'self'" in sources
    assert f"'sha256-{digest}'" in sources
    assert "unsafe-inline" not in sources


def test_the_bff_never_emits_an_empty_frame_ancestors_directive() -> None:
    """The BFF selects the directive from the reviewed registry, so it is never blank.

    A policy naming no ancestor is refused when the registry is built, and any request that
    resolves to no policy (or to a policy with a finding) gets ``'none'``, which is a real
    maximally restrictive value rather than an empty directive.
    """
    from journey_portal.domain.embed_policy import TenantEmbedPolicyService
    from journey_portal.domain.models import TenantEmbedPolicy

    empty = TenantEmbedPolicy(
        policy_id="empty-ancestors",
        tenant="fictional-bank",
        hosts=("journey.fictional-bank.test",),
        frame_ancestors=(),
        cors_origins=(),
    )
    with pytest.raises(ValueError, match="frame ancestors"):
        TenantEmbedPolicyService((empty,))

    service = TenantEmbedPolicyService(
        (
            TenantEmbedPolicy(
                policy_id="fictional-bank-v1",
                tenant="fictional-bank",
                hosts=("journey.fictional-bank.test",),
                frame_ancestors=("'self'",),
                cors_origins=(),
            ),
        )
    )
    resolved = service.assess(
        request_host="journey.fictional-bank.test",
        tenant="fictional-bank",
    )
    assert resolved.frame_ancestors == ("'self'",)
    unknown = service.assess(request_host="unknown.test", tenant="fictional-bank")
    assert unknown.frame_ancestors == ("'none'",)
    for assessment in (resolved, unknown):
        assert " ".join(assessment.frame_ancestors).strip(), "a blank directive is a parse error"


_RM_NEXT_CONFIG = Path("ui-rm/next.config.mjs").read_text(encoding="utf-8")


def test_rm_dev_shell_proxies_doc1_canonical_agent_mount() -> None:
    assert 'source: "/agent/:path*"' in _RM_NEXT_CONFIG
    assert "destination: `${bff}/agent/:path*`" in _RM_NEXT_CONFIG


def test_the_rm_shell_config_reads_frame_ancestors_in_three_states() -> None:
    """Drift guard: `|| "'self'"` silently repairs a blank instead of surfacing it."""
    assert "function resolveFrameAncestors" in _RM_NEXT_CONFIG
    assert "resolveFrameAncestors(process.env.NEXT_PUBLIC_FRAME_ANCESTORS)" in _RM_NEXT_CONFIG
    assert "NEXT_PUBLIC_FRAME_ANCESTORS ||" not in _RM_NEXT_CONFIG


def test_the_rm_shell_config_refuses_an_emptied_frame_ancestors() -> None:
    """The behavioural half of the guard above, when a Node runtime is available."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI and dev machines both ship Node
        pytest.skip("node is not installed")
    script = "import('./ui-rm/next.config.mjs').then(() => process.exit(0))"

    unset = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=False, env=_node_env(None)
    )
    assert unset.returncode == 0, unset.stderr

    emptied = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=False, env=_node_env("")
    )
    assert emptied.returncode != 0, "an emptied frame-ancestors allowlist must refuse to load"
    assert "NEXT_PUBLIC_FRAME_ANCESTORS" in emptied.stderr
    assert "'none'" in emptied.stderr, "the refusal must name the way to express a lockdown"


@pytest.mark.parametrize("spelling", ["*", "'*'", "null", "*.*"])
def test_the_rm_shell_config_refuses_a_wildcard_frame_ancestor(spelling: str) -> None:
    """The document a browser frames is served by this shell, not by the BFF.

    The BFF refuses a wildcard twice over (``domain/embed_policy.py`` and
    ``deployment_config.py`` both demand exact HTTPS origins), and the two static shells refuse
    it in ``static_server.py``. This config was the one emitter that passed one through, so a
    deployment could still have shipped ``frame-ancestors *`` on the shell's own document and
    been framed by any site.

    Four spellings, not one: ``'*'`` is the quoted form CSP also honours, ``*.*`` is the
    subdomain wildcard, and ``null`` is what a sandboxed iframe presents as its origin, so
    allowing it is a real bypass rather than a typo.
    """
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI and dev machines both ship Node
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "-e", "import('./ui-rm/next.config.mjs').then(() => process.exit(0))"],
        capture_output=True,
        text=True,
        check=False,
        env=_node_env(spelling),
    )
    assert result.returncode != 0, f"{spelling!r} must refuse to load"
    assert "NEXT_PUBLIC_FRAME_ANCESTORS" in result.stderr
    assert "wildcard" in result.stderr


def test_the_rm_shell_config_still_accepts_a_named_parent_origin() -> None:
    """The refusal must not cost the shell its actual embedding configuration."""
    node = shutil.which("node")
    if node is None:  # pragma: no cover - CI and dev machines both ship Node
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "-e", "import('./ui-rm/next.config.mjs').then(() => process.exit(0))"],
        capture_output=True,
        text=True,
        check=False,
        env=_node_env("'self' https://portal.fictional-bank.test"),
    )
    assert result.returncode == 0, result.stderr


def _node_env(frame_ancestors: str | None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("NEXT_PUBLIC_FRAME_ANCESTORS", None)
    if frame_ancestors is not None:
        env["NEXT_PUBLIC_FRAME_ANCESTORS"] = frame_ancestors
    return env
