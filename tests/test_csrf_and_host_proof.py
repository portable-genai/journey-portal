"""CSRF tokens, session binding and the host authorization proof: the user-intent evidence."""

from __future__ import annotations

import json

import pytest

from journey_portal.domain.csrf import (
    TOKEN_TTL_SECONDS,
    CsrfError,
    mint_csrf_token,
    session_binding,
    verify_csrf_token,
)
from journey_portal.domain.doc1_broker import (
    ID_TOKEN_SUBJECT_TYPE,
    BrokerPolicyError,
    Doc1BrokerPolicy,
    HostAuthorizationProof,
    HostProofRejected,
    assess_browser_provenance,
    build_grant_request,
    build_host_proof,
)
from journey_portal.domain.jose import b64u_decode, b64u_encode, compact_json

SECRET = b"portal-session-signing-key-fixture"
GRANT_PATH = "/v1/doc1/embed/grant"
NOW = 1_786_000_000
INSTANCE = "instance-fixture-0123456789"


def _policy(**overrides: object) -> Doc1BrokerPolicy:
    fields: dict[str, object] = {
        "grant_endpoint": "https://doc1.example/agent/api/v1/embed/grants",
        "installation_id": "inst_fixture",
        "bff_client_id": "hrz9-journey-portal-bff-fixture",
        "portal_origin": "https://portal.example",
        "requested_scopes": ("cdd.embed", "cdd.read"),
    }
    fields.update(overrides)
    return Doc1BrokerPolicy(**fields)  # type: ignore[arg-type]


def _binding(subject: str = "analyst@bank.example", tenant: str = "demo-bank") -> str:
    return session_binding(SECRET, subject=subject, tenant=tenant)


def _token(binding: str | None = None, *, now: int = NOW) -> str:
    return mint_csrf_token(
        SECRET,
        binding=binding if binding is not None else _binding(),
        method="POST",
        path=GRANT_PATH,
        issued_at=now,
        nonce="n" * 24,
    )


# --------------------------------------------------------------------------- session binding
def test_the_session_binding_is_a_sha256_hex_digest_and_hides_the_subject() -> None:
    binding = _binding()
    assert len(binding) == 64 and all(character in "0123456789abcdef" for character in binding)
    assert "analyst@bank.example" not in binding
    assert binding != session_binding(SECRET, subject="other@bank.example", tenant="demo-bank")
    assert binding != session_binding(SECRET, subject="analyst@bank.example", tenant="other-bank")


def test_the_binding_is_unambiguous_across_subject_and_tenant_boundaries() -> None:
    """Length-prefixed, so ``a|bc`` and ``ab|c`` cannot collide into the same binding."""
    assert session_binding(SECRET, subject="ab", tenant="c") != session_binding(
        SECRET, subject="a", tenant="bc"
    )


def test_a_missing_secret_or_identity_refuses() -> None:
    with pytest.raises(CsrfError):
        session_binding(b"", subject="a", tenant="b")
    with pytest.raises(CsrfError):
        session_binding(SECRET, subject="", tenant="b")


# --------------------------------------------------------------------------- CSRF tokens
def test_a_token_verifies_for_the_session_and_action_it_was_minted_for() -> None:
    verify_csrf_token(_token(), SECRET, binding=_binding(), method="POST", path=GRANT_PATH, now=NOW)


def test_a_token_from_another_session_is_refused() -> None:
    other = session_binding(SECRET, subject="other@bank.example", tenant="demo-bank")
    with pytest.raises(CsrfError):
        verify_csrf_token(
            _token(other), SECRET, binding=_binding(), method="POST", path=GRANT_PATH, now=NOW
        )


def test_a_token_for_another_action_is_refused() -> None:
    with pytest.raises(CsrfError):
        verify_csrf_token(
            _token(), SECRET, binding=_binding(), method="DELETE", path=GRANT_PATH, now=NOW
        )
    with pytest.raises(CsrfError):
        verify_csrf_token(
            _token(), SECRET, binding=_binding(), method="POST", path="/v1/other", now=NOW
        )


def test_an_expired_token_is_refused_and_a_stretched_lifetime_does_not_help() -> None:
    with pytest.raises(CsrfError):
        verify_csrf_token(
            _token(),
            SECRET,
            binding=_binding(),
            method="POST",
            path=GRANT_PATH,
            now=NOW + TOKEN_TTL_SECONDS + 1,
        )
    # Re-signing a payload whose exp was widened still fails: the lifetime is asserted, not read.
    payload = {
        "v": 1,
        "nonce": "n" * 24,
        "iat": NOW,
        "exp": NOW + TOKEN_TTL_SECONDS * 10,
        "method": "POST",
        "path": GRANT_PATH,
    }
    import hashlib
    import hmac

    encoded = b64u_encode(compact_json(payload))
    key = hmac.new(
        SECRET, b"hrz9-doc1-grant-csrf\x00" + _binding().encode("ascii"), hashlib.sha256
    ).digest()
    signature = hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest()
    with pytest.raises(CsrfError):
        verify_csrf_token(
            f"{encoded}.{b64u_encode(signature)}",
            SECRET,
            binding=_binding(),
            method="POST",
            path=GRANT_PATH,
            now=NOW,
        )


def test_a_tampered_or_absent_token_is_refused() -> None:
    encoded, signature = _token().split(".", 1)
    payload = json.loads(b64u_decode(encoded))
    payload["path"] = "/v1/other"
    forged = f"{b64u_encode(compact_json(payload))}.{signature}"
    for candidate in ("", "not-a-token", forged, _token()[:-3]):
        with pytest.raises(CsrfError):
            verify_csrf_token(
                candidate, SECRET, binding=_binding(), method="POST", path=GRANT_PATH, now=NOW
            )


def test_tokens_are_only_issued_for_unsafe_methods_and_sane_paths() -> None:
    with pytest.raises(CsrfError, match="unsafe"):
        mint_csrf_token(
            SECRET, binding=_binding(), method="GET", path=GRANT_PATH, issued_at=NOW, nonce="n" * 24
        )
    for path in ("relative", "//evil", "/with?query", "/with#fragment", "/" + "p" * 512):
        with pytest.raises(CsrfError, match="path"):
            mint_csrf_token(
                SECRET, binding=_binding(), method="POST", path=path, issued_at=NOW, nonce="n" * 24
            )


# --------------------------------------------------------------------------- browser provenance
def test_only_an_exact_same_origin_script_call_passes() -> None:
    assess_browser_provenance(
        _policy(), origin="https://portal.example", fetch_site="same-origin", fetch_mode="cors"
    )


@pytest.mark.parametrize(
    ("origin", "fetch_site"),
    [
        ("https://portal.example.attacker.test", "same-origin"),
        ("https://attacker.test", "same-origin"),
        ("", "same-origin"),
        ("https://portal.example", "cross-site"),
        ("https://portal.example", "same-site"),
        ("https://portal.example", ""),
    ],
)
def test_a_cross_site_or_unlabelled_request_is_refused(origin: str, fetch_site: str) -> None:
    with pytest.raises(HostProofRejected):
        assess_browser_provenance(_policy(), origin=origin, fetch_site=fetch_site)


def test_a_navigation_or_document_request_is_refused() -> None:
    with pytest.raises(HostProofRejected):
        assess_browser_provenance(
            _policy(),
            origin="https://portal.example",
            fetch_site="same-origin",
            fetch_mode="navigate",
        )
    with pytest.raises(HostProofRejected):
        assess_browser_provenance(
            _policy(),
            origin="https://portal.example",
            fetch_site="same-origin",
            fetch_dest="document",
        )


# --------------------------------------------------------------------------- the proof itself
def test_the_proof_carries_the_verified_subject_and_the_reviewed_origin() -> None:
    proof = build_host_proof(
        _policy(),
        subject="analyst@bank.example",
        binding=_binding(),
        user_intent_id="u" * 24,
    )
    payload = proof.as_payload()
    assert payload["host_origin"] == "https://portal.example"
    assert payload["fetch_site"] == "same-origin"
    assert payload["csrf_verified"] is True
    assert payload["session_source_subject"] == "analyst@bank.example"
    assert set(payload) == {
        "host_origin",
        "fetch_site",
        "csrf_verified",
        "session_binding",
        "session_source_subject",
        "user_intent_id",
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"fetch_site": "cross-site"},
        {"csrf_verified": False},
        {"session_binding": "not-a-digest"},
        {"user_intent_id": "short"},
        {"user_intent_id": "has space in it here"},
        {"session_source_subject": ""},
    ],
)
def test_a_proof_doc1_would_reject_cannot_be_constructed(overrides: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "host_origin": "https://portal.example",
        "fetch_site": "same-origin",
        "csrf_verified": True,
        "session_binding": _binding(),
        "session_source_subject": "analyst@bank.example",
        "user_intent_id": "u" * 24,
    }
    fields.update(overrides)
    with pytest.raises(HostProofRejected):
        HostAuthorizationProof(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the grant body
def test_the_grant_body_is_the_shape_doc1_accepts() -> None:
    policy = _policy()
    proof = build_host_proof(
        policy, subject="analyst@bank.example", binding=_binding(), user_intent_id="u" * 24
    )
    body = build_grant_request(
        policy,
        instance_id=INSTANCE,
        subject_token="subject-token-fixture",
        client_assertion="assertion-fixture",
        proof=proof,
    )
    assert set(body) == {
        "installation_id",
        "instance_id",
        "client_id",
        "client_assertion_type",
        "client_assertion",
        "subject_token_type",
        "subject_token",
        "requested_scopes",
        "host_proof",
    }
    assert body["subject_token_type"] == ID_TOKEN_SUBJECT_TYPE
    assert body["requested_scopes"] == ["cdd.embed", "cdd.read"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"grant_endpoint": ""},
        {"grant_endpoint": "doc1.example/grants"},
        {"installation_id": ""},
        {"bff_client_id": ""},
        {"portal_origin": ""},
        {"portal_origin": "https://portal.example/"},
        {"portal_origin": "https://portal.example/agent"},
        {"requested_scopes": ()},
        {"requested_scopes": ("cdd.embed", "cdd.embed")},
        {"requested_scopes": ("has space",)},
        {"subject_token_type": "urn:example:not-reviewed"},
    ],
)
def test_an_incomplete_broker_policy_refuses_rather_than_defaulting(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(BrokerPolicyError):
        _policy(**overrides)


def test_an_out_of_range_instance_or_token_is_refused() -> None:
    policy = _policy()
    proof = build_host_proof(
        policy, subject="analyst@bank.example", binding=_binding(), user_intent_id="u" * 24
    )
    for kwargs in (
        {"instance_id": "too-short"},
        {"subject_token": ""},
        {"client_assertion": ""},
        {"client_assertion": "a" * 16385},
    ):
        arguments: dict[str, object] = {
            "instance_id": INSTANCE,
            "subject_token": "subject-token-fixture",
            "client_assertion": "assertion-fixture",
            "proof": proof,
        }
        arguments.update(kwargs)
        with pytest.raises(BrokerPolicyError):
            build_grant_request(policy, **arguments)  # type: ignore[arg-type]
