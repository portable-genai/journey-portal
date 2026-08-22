# On-prem migration: the reversibility path

The catalog's reversibility principle (P-12) requires a documented exit from the managed cloud. For
the portal that means identity, egress and access-evidence seams. All are `onprem` adapter
placeholders today (they satisfy the port and fail fast), so the shape of the exit is explicit and
unit-proven, and a client fills in concrete adapters.

## What the portal needs from an on-prem environment

The portal is a reverse proxy plus an identity gate and content-free access evidence. It needs:

1. **An identity source** to verify a request and resolve a `Principal` (the client's own IdP).
2. **A network egress path** to reach the embedded app services (service mesh, internal LB).
3. **An append-only evidence sink** for pseudonymized access metadata and retained integrity
   checkpoints.

It stores no business data, request bodies, queries, credentials or identity assertions.

The RM and Ops images are ordinary OCI artifacts. Replace the managed load balancer with the
institution's HTTPS/IAP-equivalent edge, retain the same host/path routing, and serve both images
without rebuilding their application contract. The seven embedded applications remain independent
reviewed UI/API images.

## Seam 1: identity (`adapters/onprem/identity.py`)

The placeholder raises `NotImplementedError`. Replace it with an adapter that resolves a verified
`Principal` from the client's IdP. Two common cases:

- **Reverse-proxy header identity.** If an on-prem gateway (for example an OIDC-aware nginx, or a
  SAML proxy) terminates auth and injects a verified header, read and trust that header (only when
  the gateway is the sole ingress) and map it to a `Principal`. This mirrors the `gcp` IAP adapter,
  with the client's gateway in place of IAP.
- **Bearer / JWKS verification.** If the client forwards a signed token, verify it against the IdP's
  JWKS (issuer + audience + algorithm allowlist, fail closed) and map the claims to a `Principal`.
  Keep any crypto dependency lazy so the SDK-free profiles stay import-clean.

The rest of the portal is unchanged: the same identity-injection policy then presents that verified
identity to each embedded app.

## Seam 2: egress (`adapters/onprem/upstream.py`)

The placeholder raises `NotImplementedError`. Replace it with a forwarder that reaches the app
services over the on-prem network. The interface is one method (`forward`) returning an
`UpstreamResponse`; an httpx client pointed at the internal service URLs is usually enough, plus any
mesh mTLS the environment requires. The `local`/`gcp` httpx adapter is a working reference.

## Seam 3: access evidence (`adapters/onprem/access_audit.py`)

The placeholder raises `NotImplementedError`. Replace it with an adapter for the institution's
append-only or WORM evidence sink. Preserve the `AccessAuditPort` contract, the pseudonymized
event fields and an independently retained integrity checkpoint. Preserve the local reference's
cross-process serialization and recoverable pending-checkpoint semantics if the selected database
and checkpoint cannot commit in one transaction. The local SQLite adapter is the executable
hash-chain reference; the managed adapter is the structured-log reference.

## Seam 4: BFF signing key (`adapters/onprem/bff_credentials.py`)

The placeholder raises `NotImplementedError`. Replace it with an adapter over the institution's
own HSM or key vault. Preserve the `BffSigningKeyPort` contract: the private key must stay inside
its custody (the port only ever returns a public JWK and a signature over caller-supplied bytes),
the key id must be stable across restarts because a relying party pins it, and `published_keys`
must include the still-accepted rotation-window keys so a rotation causes no token outage. The
Cloud KMS adapter is the reference for a non-exportable signer; the local file-backed adapter is
the executable reference for the RS256 encoding.

## Seam 5: end-user subject token (`adapters/onprem/subject_token.py`)

The placeholder raises `NotImplementedError`. Replace it with an adapter that obtains, from the
client's own identity provider, a token identifying the user whose portal session has already
been verified. The token must satisfy whatever issuer profile the far side reviewed; it is never
minted here and never held by the browser.

## Config

Point `config/journeys.yaml` upstreams at the internal service URLs (via the `${ENV:-default}`
overrides), set `PORTAL_PROFILE=onprem`, and bind the adapters in `config.py`'s `_BINDINGS`
(the `onprem` rows). The parity contract test already asserts the seams exist and fail closed; make
it pass with the real adapters and the exit is complete.

## What does not need to move

The domain core (journey catalog, identity-injection policy, routing) is pure stdlib and runs
anywhere unchanged. The shells are static builds served by any web server behind the client's edge.
Only the adapters above are environment-specific.

## Evidence needed before calling the exit complete

The current `onprem` bindings deliberately fail fast. A real exit must add IdP verification,
authenticated/mTLS egress, an institution-reviewed `PORTAL_TENANT_EMBED_POLICIES_JSON` with each
routed host bound exactly once, image-mirror provenance, secret custody, regional access evidence
and browser tests for both journeys. Serve the same policy document to both static shells as
`TENANT_EMBED_POLICIES_JSON`. Run `scripts/portability_demo.py` before and after the adapter work:
today it proves the seam and fail-fast behavior, not a working migration.
