"""Why this repository keeps its own tenant resolution, proved by running both.

Every other user-facing repository in this fleet now ends `resolve()` with one
:func:`hex_service_kit.federation.principal_from_iap_claims` call under a reviewed
:class:`~hex_service_kit.federation.FederationPolicy`. This one was the obvious candidate to
follow, because it is the one deployment that actually configures a reviewed domain map
(``PORTAL_TENANT_DOMAINS``, parsed in ``config.py``) rather than taking the hosted domain as
the tenant. Adoption executed the two side by side over the same claim sets, and the map is
not the part that disagrees.

**The blocker is the MACHINE caller, and it empties a load-bearing tenant.**
``FederationPolicy.tenant_for`` short circuits on ``machine=True`` and returns
``machine_tenant``, one string for every machine caller, BEFORE ``domain_tenants`` is
consulted at all. This deployment maps a service account's own domain
(``<project>.iam.gserviceaccount.com``) onto a reviewed tenant, exactly as
:meth:`IapIdentityAdapter._tenant_for` documents, so under the commons every mapped machine caller
would resolve to no tenant. That is fail-closed and closed for a whole population, and no
offline gate would see it, because the local profile never constructs this adapter.

**The second disagreement runs the other way.** ``tenant_for`` consults the reviewed map for
the asserted domain AND for the mail domain, and returns the first that is mapped. This
adapter consults exactly one: ``hd`` if present, otherwise the mail domain. So a caller whose
``hd`` is present but UNMAPPED, and whose mail domain is mapped, goes from no tenant to a
mapped one. The commons has a reason (every entry is a domain a deployment wrote down by
name), and it is still a widening measured against what ships here, so it is not taken
silently either.

Neither is a defect in the commons. They are two deployments that are right about different
things, and the finding belongs in the kit's backlog as a missing ``machine_tenants`` map
rather than in a pull request that quietly changes who this service serves.

The rows below run both and assert the disagreement, so this is an exclusion the suite checks
rather than a comment somebody has to believe. The transport half IS adopted, and the last
row says so.
"""

from __future__ import annotations

from typing import Any

from hex_service_kit.federation import (
    IAP_ISSUER,
    FederationPolicy,
    principal_from_iap_claims,
)

from journey_portal.adapters.gcp.identity import IapIdentityAdapter

_MAP = {
    "reference-bank.test": "reference-bank",
    "demo-project.iam.gserviceaccount.com": "reference-bank",
}


class _Settings:
    """Only the two fields the tenant decision reads."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self.tenant_by_domain = mapping
        self.iap_audience = "/projects/1234567890/global/backendServices/42"


def _shipped(mapping: dict[str, str], claims: dict[str, Any]) -> str:
    adapter = object.__new__(IapIdentityAdapter)
    adapter._settings = _Settings(mapping)
    return adapter._tenant_for(claims, str(claims.get("email", "")))


def _commons(mapping: dict[str, str], claims: dict[str, Any]) -> str:
    policy = FederationPolicy(
        domain_tenants=mapping,
        # Passthrough only where no map is configured, which is what preserves this
        # repository's own "with no map this returns the domain" behaviour.
        tenant_from_hosted_domain=not mapping,
    )
    full = {"iss": IAP_ISSUER, "sub": "accounts.google.com:1", **claims}
    return principal_from_iap_claims(full, policy, source="gcp-iap").tenant


# --------------------------------------------------------------------------------------- #
# Where the two agree, which is most of it, and why the exclusion is narrow.
# --------------------------------------------------------------------------------------- #
def test_a_mapped_hosted_domain_resolves_identically() -> None:
    claims = {"hd": "reference-bank.test", "email": "avery.stone@reference-bank.test"}
    assert _shipped(_MAP, claims) == "reference-bank"
    assert _commons(_MAP, claims) == "reference-bank"


def test_an_unmapped_identity_resolves_to_no_tenant_in_both() -> None:
    claims = {"hd": "somewhere-else.test", "email": "someone@somewhere-else.test"}
    assert _shipped(_MAP, claims) == ""
    assert _commons(_MAP, claims) == ""


# --------------------------------------------------------------------------------------- #
# (1) The blocker: a reviewed machine caller loses its tenant under the commons.
# --------------------------------------------------------------------------------------- #
def test_a_reviewed_machine_caller_keeps_its_mapped_tenant_here_and_loses_it_there() -> None:
    """``tenant_for`` returns ``machine_tenant`` before ``domain_tenants`` is consulted."""
    claims = {"email": "portal-runner@demo-project.iam.gserviceaccount.com"}
    assert _shipped(_MAP, claims) == "reference-bank"
    assert _commons(_MAP, claims) == ""


def test_the_commons_cannot_express_a_per_project_machine_tenant() -> None:
    """``machine_tenant`` is one string, so two reviewed projects cannot map to two tenants.

    Stated directly rather than inferred, because it is the reason this is a kit gap and not a
    configuration mistake: filling ``machine_tenant`` in would give every machine caller the
    same tenant, including one from a project nobody reviewed.
    """
    policy = FederationPolicy(domain_tenants=_MAP, machine_tenant="reference-bank")
    unreviewed = {
        "iss": IAP_ISSUER,
        "sub": "accounts.google.com:1",
        "email": "stranger@other-project.iam.gserviceaccount.com",
    }
    assert principal_from_iap_claims(unreviewed, policy).tenant == "reference-bank"
    assert _shipped(_MAP, {"email": unreviewed["email"]}) == ""


# --------------------------------------------------------------------------------------- #
# (2) The widening: two reviewed domains on one identity.
# --------------------------------------------------------------------------------------- #
def test_an_unmapped_hosted_domain_is_not_rescued_by_a_mapped_mail_domain_here() -> None:
    """This adapter reads ``hd`` OR the mail domain; the commons reads ``hd`` AND then it."""
    claims = {"hd": "contractor.test", "email": "avery.stone@reference-bank.test"}
    assert _shipped(_MAP, claims) == ""
    assert _commons(_MAP, claims) == "reference-bank"


# --------------------------------------------------------------------------------------- #
# The transport half IS adopted. The exclusion is the claim half alone.
# --------------------------------------------------------------------------------------- #
def test_the_transport_facts_are_rebound_from_the_commons_and_not_re_declared() -> None:
    """Value equality is not enough here, so the SOURCE is asserted.

    This module kept its own three literals until 2026-08-26 while
    ``domain/identity_injection.py`` a few directories away had taken them from the commons
    since tier 3 landed. Nothing noticed, and an equality assertion would not have: a local
    copy always agrees with itself on the day it is written. Two modules in one repository
    disagreeing about which header carries identity is exactly the drift the kit exists to
    make impossible, so what is checked is that no copy is here to drift.
    """
    import inspect

    from hex_service_kit import federation as kit

    from journey_portal.adapters.gcp import identity

    assert identity._IAP_ASSERTION_HEADER == kit.IAP_ASSERTION_HEADER
    assert identity._IAP_ISSUER == kit.IAP_ISSUER
    assert identity._IAP_CERTS_URL == kit.IAP_KEYS_URL

    source = inspect.getsource(identity)
    for literal in (kit.IAP_ASSERTION_HEADER, kit.IAP_ISSUER, kit.IAP_KEYS_URL):
        assert f'"{literal}"' not in source, f"{literal} is re-declared rather than rebound"
