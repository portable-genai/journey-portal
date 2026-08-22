"""Deterministic tenant host, framing and CORS policy tests."""

from __future__ import annotations

import pytest

from journey_portal.domain.embed_policy import TenantEmbedPolicyService
from journey_portal.domain.models import EmbedPolicyFindingKind, TenantEmbedPolicy


def _policy(**changes: object) -> TenantEmbedPolicy:
    values: dict[str, object] = {
        "policy_id": "fictional-bank-policy-v1",
        "tenant": "fictional-bank",
        "hosts": ("journey.fictional-bank.test",),
        "frame_ancestors": ("'self'", "https://host.fictional-bank.test"),
        "cors_origins": ("https://host.fictional-bank.test",),
    }
    values.update(changes)
    return TenantEmbedPolicy(**values)  # type: ignore[arg-type]


def test_same_origin_policy_is_allowed_and_deterministic() -> None:
    service = TenantEmbedPolicyService((_policy(),))

    first = service.assess(
        request_host="journey.fictional-bank.test",
        tenant="fictional-bank",
    )
    repeated = service.assess(
        request_host="journey.fictional-bank.test",
        tenant="fictional-bank",
    )

    assert first == repeated
    assert first.decision == "allowed"
    assert first.escalates is False
    assert first.frame_ancestors == ("'self'", "https://host.fictional-bank.test")
    assert first.cors_allowed is False


def test_exact_reviewed_cross_origin_is_allowed() -> None:
    assessment = TenantEmbedPolicyService((_policy(),)).assess(
        request_host="journey.fictional-bank.test",
        tenant="fictional-bank",
        request_origin="https://host.fictional-bank.test",
    )

    assert assessment.decision == "allowed"
    assert assessment.cors_allowed is True
    assert assessment.findings == ()


@pytest.mark.parametrize(
    ("host", "tenant", "origin", "kind"),
    [
        (
            "unknown.fictional-bank.test",
            "fictional-bank",
            "",
            EmbedPolicyFindingKind.UNKNOWN_HOST,
        ),
        (
            "journey.fictional-bank.test",
            "other-bank",
            "",
            EmbedPolicyFindingKind.TENANT_HOST_MISMATCH,
        ),
        (
            "journey.fictional-bank.test",
            "fictional-bank",
            "https://attacker.test",
            EmbedPolicyFindingKind.ORIGIN_DENIED,
        ),
    ],
)
def test_policy_mismatches_fail_closed_with_evidence(
    host: str,
    tenant: str,
    origin: str,
    kind: EmbedPolicyFindingKind,
) -> None:
    assessment = TenantEmbedPolicyService((_policy(),)).assess(
        request_host=host,
        tenant=tenant,
        request_origin=origin,
    )

    assert assessment.decision == "denied"
    assert assessment.escalates is True
    assert assessment.frame_ancestors == ("'none'",)
    assert assessment.cors_allowed is False
    assert [finding.kind for finding in assessment.findings] == [kind]
    assert assessment.findings[0].evidence_id
    assert assessment.suggested_actions


def test_duplicate_host_bindings_are_rejected() -> None:
    with pytest.raises(ValueError, match="resolve exactly once"):
        TenantEmbedPolicyService(
            (
                _policy(),
                _policy(
                    policy_id="other-policy",
                    tenant="other-bank",
                ),
            )
        )


def test_wildcard_tenant_is_local_only() -> None:
    with pytest.raises(ValueError, match="local profile"):
        TenantEmbedPolicyService((_policy(tenant="*"),))

    local = TenantEmbedPolicyService(
        (
            _policy(
                tenant="*",
                hosts=("localhost",),
                frame_ancestors=("'self'",),
                cors_origins=("http://localhost:3000",),
            ),
        ),
        allow_local_wildcard=True,
    )
    assert local.assess(request_host="localhost", tenant="fictional-bank").decision == "allowed"


@pytest.mark.parametrize(
    "origin",
    [
        "https://host.fictional-bank.test/",
        "https://invalid_host.test",
        "https://[::1]",
        "https://a..fictional-bank.test",
        "https://host.fictional-bank.test:443",
        "https://HOST.fictional-bank.test",
    ],
)
def test_origin_contract_rejects_values_the_static_shells_cannot_apply(origin: str) -> None:
    with pytest.raises(ValueError, match="origin"):
        TenantEmbedPolicyService(
            (
                _policy(
                    frame_ancestors=(origin,),
                    cors_origins=(origin,),
                ),
            )
        )


@pytest.mark.parametrize("spelling", ["*", "'*'", "null", "*.*", "https://*.fictional-bank.test"])
def test_every_wildcard_spelling_is_refused_in_both_origin_lists(spelling: str) -> None:
    """The refusal the service already made, now stated rather than implied.

    ``_exact_origin`` rejects a literal asterisk outright and everything else here by demanding
    an exact HTTPS origin, so all five spellings were already refused. None of them was named in
    a test, which is the difference between a property and an accident: ``null`` in particular
    is the origin a SANDBOXED iframe presents, so a future relaxation of the origin contract
    that let it through would be a real bypass with nothing to catch it. The shell configs and
    ``deployment_config.py`` refuse the same set on their own surfaces.
    """
    for field in ("frame_ancestors", "cors_origins"):
        with pytest.raises(ValueError, match="origin"):
            TenantEmbedPolicyService((_policy(**{field: (spelling,)}),))
