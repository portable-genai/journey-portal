"""The trust boundary, asserted against the deployment rather than against a fixture.

Track C item 4: Hrz9 was exercised against the real services by running it, which found
defects no offline profile can reach. That proves those paths worked at the moment they were
run, and nothing re-runs them.

The offline suite already asserts the header-rewrite plan thoroughly, and it cannot assert the
part that actually broke. Every defect the first deployment found was a property of the HOP,
not of the plan:

* ``x-goog-*`` is stripped by the serverless frontend on the way into a service, so the portal
  could not hand an embedded app the assertion IAP had given it under the standard name. The
  plan was correct. The app answered "missing IAP assertion header; request did not pass
  through IAP" about a request that had passed through IAP one hop earlier.
* ``x-serverless-authorization`` is INJECTED by the platform, holding a token minted for this
  service. Nothing in an offline fixture contains it, so no offline test can forget to strip
  it -- and forwarded verbatim it produces a 401 from a callee whose IAM binding, ingress and
  caller token are all correct.
* ``/healthz`` is answered by the frontend and never reaches the container, so a proxied
  readiness probe checks the platform rather than the app.

None of the three is visible without a real edge in front of a real service. That is what this
module is for, and why it is not a duplicate of ``test_identity_injection.py``.

**Three states, never two.** No deployment named skips, so the offline gate is unaffected; a
deployment named and reachable runs; a deployment named and NOT usable **fails**. A managed
suite that skips when its configuration is wrong reports the same green as one that ran, which
is the served-browser defect wearing different clothes.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request

import pytest

_BASE_VAR = "PORTAL_MANAGED_TEST_BASE_URL"
_AUDIENCE_VAR = "PORTAL_E2E_IAP_AUDIENCE"
_ACCOUNT_VAR = "PORTAL_E2E_SERVICE_ACCOUNT"


def _named_target() -> str | None:
    """The deployment under test: absent is "not asked for", empty is a configuration defect."""
    if _BASE_VAR not in os.environ:
        return None
    value = os.environ[_BASE_VAR].strip()
    if not value:
        raise AssertionError(
            f"{_BASE_VAR} is set to an empty value. Leave it unset to skip the managed suite, "
            f"or name the origin you mean. A blank setting is how a rendered template turns a "
            f"managed test run into a skip nobody notices."
        )
    if not value.startswith("https://"):
        raise AssertionError(
            f"{_BASE_VAR} must be https for a target behind IAP, got {value!r}: an identity "
            f"token handed to a plaintext origin is a credential on the wire."
        )
    return value.rstrip("/")


_BASE = _named_target()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _BASE is None,
        reason=f"set {_BASE_VAR} to run the managed trust-boundary suite against a deployment",
    ),
]


@pytest.fixture(scope="module")
def token() -> str:
    """An IAP-accepted identity token, minted without any human credential.

    Reuses the mechanism ``e2e/targets.py`` already uses, so this suite and the browser
    journey authenticate the same way and a change to one cannot silently diverge.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))
    from targets import _mint_iap_token  # noqa: PLC2701 - deliberately the same minting path

    account = os.environ.get(_ACCOUNT_VAR, "").strip()
    audience = os.environ.get(_AUDIENCE_VAR, "").strip()
    if not account or not audience:
        raise AssertionError(
            f"{_BASE_VAR} names a deployment, so {_ACCOUNT_VAR} and {_AUDIENCE_VAR} are "
            f"required. Refusing rather than skipping: a managed suite that skips on missing "
            f"configuration reports the same green as one that ran."
        )
    return _mint_iap_token(account, audience)


def _get(path: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(f"{_BASE}{path}", headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read()


# --------------------------------------------------------------------------------------- #
# The edge refuses what it should.
# --------------------------------------------------------------------------------------- #
def test_an_unauthenticated_request_never_reaches_the_app() -> None:
    """IAP, asserted rather than assumed. No token at all."""

    status, _, _ = _get("/", {})

    assert status in (302, 401, 403), (
        f"an unauthenticated request to the deployment returned {status}. The portal is "
        f"supposed to be unreachable without an identity the edge verified."
    )


def test_a_forged_assertion_header_does_not_authenticate() -> None:
    """The reserved namespace, proved from the attacker's side.

    A browser setting ``x-goog-iap-jwt-assertion`` must gain nothing, because the frontend
    strips it entering the service. This asserts the platform behaviour the whole forwarding
    design rests on -- if it ever stopped holding, the portal's own header rewrite would be
    the only thing standing between a browser and an asserted identity.
    """

    status, _, _ = _get("/", {"x-goog-iap-jwt-assertion": "forged.by.a.browser"})

    assert status in (302, 401, 403), (
        f"a forged IAP assertion header produced {status}: the serverless frontend is not "
        f"stripping the reserved namespace, and the forwarding design assumes it does."
    )


# --------------------------------------------------------------------------------------- #
# The hop into an embedded app.
# --------------------------------------------------------------------------------------- #
def test_an_authenticated_request_reaches_the_portal(token: str) -> None:
    status, _, body = _get("/", {"Authorization": f"Bearer {token}"})

    assert status == 200, f"an authenticated request returned {status}"
    assert body, "the portal answered 200 with an empty body"


def test_the_embedded_app_is_served_same_origin(token: str) -> None:
    """Same origin is the whole embed contract: two Cloud Run URLs would be two origins."""

    status, headers, _ = _get("/agent/", {"Authorization": f"Bearer {token}"})

    assert status == 200, f"the embedded app at /agent/ returned {status}"
    assert "x-frame-options" not in {k.lower() for k in headers}, (
        "the proxied response carries X-Frame-Options, which would forbid the very embedding "
        "this path exists to serve; it is in the hop-by-hop response strip set for that reason"
    )


def test_the_embedded_app_learns_identity_through_the_portal(token: str) -> None:
    """The end-to-end assertion: the app answers with the identity the PORTAL verified.

    This is the one that would have caught the reserved-namespace defect. The plan was
    correct, the app was reachable, and the request still arrived without an assertion.
    """

    status, _, body = _get("/agent/api/v1/whoami", {"Authorization": f"Bearer {token}"})

    if status == 404:
        pytest.skip("this deployment does not expose /agent/api/v1/whoami")
    assert status == 200, f"the embedded app's identity endpoint returned {status}: {body[:400]!r}"
    assert b"@" in body, "the embedded app returned no subject for a request the portal verified"


# --------------------------------------------------------------------------------------- #
# Paths the platform reserves.
# --------------------------------------------------------------------------------------- #
def test_a_versioned_readiness_path_reaches_the_container(token: str) -> None:
    """``/healthz`` is answered by the frontend and never reaches the app.

    A container's own probe works; a PROXIED readiness check does not, and it reports healthy
    either way, which is the worst possible combination. The versioned path is the one a
    console may honestly probe, so its existence is asserted here rather than assumed.
    """

    status, _, _ = _get("/v1/healthz", {"Authorization": f"Bearer {token}"})

    assert status in (200, 404), f"unexpected status {status} from the versioned readiness path"
    if status == 404:
        pytest.fail(
            "the deployment exposes no /v1/healthz. /healthz is answered by the serverless "
            "frontend and never reaches the container, so a proxied readiness check against it "
            "measures the platform rather than the application."
        )
