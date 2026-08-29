# Hrz9 Journey Portal Shell

**Compose the built P1 apps into one UI per user.** The Journey Portal is a persona-journey host:
it drops the catalog's individual agent UIs into a single same-origin surface so a Relationship
Manager sees an **RM Journey** (CDD onboarding plus CIO advisory) and an Operations user sees an
**Ops Journey** (credit memo, trade-finance checks, compliance Q&A, and the human-review queue),
each behind one identity and one sign-on.

It exists to turn the catalog's standing claim ("every UI is an embeddable micro-frontend that
drops into your existing app with the journey intact and secure SSO") from documentation into a
**runnable proof a buyer can click**. Until this repo, the embedding machinery (embed mode,
basePath, CSP frame-ancestors, per-tenant CORS, a server-verified `IdentityPort`) shipped in every
UI repo but nothing in the workspace actually hosted one app inside another. This is that host.

## What it is, concretely

- A **FastAPI BFF** (`src/journey_portal`) that owns everything load-bearing: a same-origin
  reverse proxy in front of each embedded app, the journey config, the identity trust boundary,
  and an exact host-to-tenant framing/CORS registry. It injects the portal-verified identity into
  every embedded app, denies host/tenant/origin mismatches before side effects, and never lets a
  browser assert an identity.
- Two **deliberately thin UI shells** over that BFF, in **two different frameworks** on purpose:
  `ui-rm` (**React / Next.js**) and `ui-ops` (**Angular**). Same portal, two host stacks, zero
  shared UI code. That is the evidence that the agents drop into whatever a bank already runs; the
  small size of each shell is the integration-cost story.
- A **launcher** (`scripts/run_journeys.py`) that brings the whole thing up: every embedded app's
  backend and UI, the BFF, and both shells, with one command.

Journeys are **config, not code** (`config/journeys.yaml`): which apps compose into which journey,
and where each app's UI and backend live.

## How the embedding works (mode 1, same-origin)

Everything the browser loads is one origin (the shell's), so every embedded iframe is first-party:
no CORS, no third-party-cookie problem. Two proxy hops, both same-origin:

```
browser ──▶ shell (Next.js rewrites / Angular proxy)  ──▶ BFF ──▶ embedded app
            /apps/<id>/*   and   /v1/*                     /apps/<id>/*      -> app UI  (basePath-aware)
                                                           /apps/<id>/api/*  -> app backend (identity injected)
```

Most embedded apps are built with `NEXT_PUBLIC_BASE_PATH=/apps/<id>` and
`NEXT_PUBLIC_EMBED=1`. Doc1 instead keeps its portable artifact fixed at `/agent`: Hrz9
exposes `/agent/*`, redirects the `/apps/cdd-sow-research` compatibility entry to `/agent/`, and
reports `/agent/api` to the shell. The launcher selects Doc1's native channel plus
loopback `local-persona` or hosted IAP identity explicitly. Cross-repo build, proxy,
asset, API, identity, and RM-journey tests cover this contract.

For hosted deployment, `ui_build_base_path` records that build-time contract beside each UI
digest. Terraform does not present it as a runtime override, and the live workflow loads every
iframe route plus its emitted same-origin assets before accepting the deployment.

## Identity (kept deliberately simple)

| Environment | Sign-in | Mechanism |
|---|---|---|
| **Local** (default) | Seeded personas, fully offline, no IdP | The portal's persona picker sets a cookie; the BFF injects the matching `X-Dev-Persona` into each embedded app (every app seeds the shared-commons personas, so the id always resolves). Instant role-switching for demos. |
| **Cloud** | SSO | Identity-Aware Proxy fronts the portal (Workforce Identity Federation for the corporate IdP). The BFF forwards the edge-signed assertion; each app re-verifies it itself (defense in depth). |

The load-bearing invariant, unit-tested and enforced in the eval gate: **a browser-asserted
identity never reaches an embedded app.** On every proxied API call the portal strips the
client-spoofable identity headers (`X-Dev-Persona`, IAP assertion, `Authorization`) and injects the
identity it verified itself. See `docs/embedding-and-identity.md` and
`src/journey_portal/domain/identity_injection.py`.

There is deliberately **no Google-account / third-party login mode**: local is offline personas,
cloud is IAP. That keeps the SDK-free gate green and the story two-line.

## Quickstart

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"        # core + dev tools; no Google Cloud SDK

make check                     # the hard gate: ruff + ruff format + mypy + pytest + eval
make demo                      # render journeys, identity, tenant policy and audit evidence
make run-api                   # the BFF alone on http://127.0.0.1:8110

# The whole demo (BFF + every embedded app + both shells) needs the sibling repos present:
make journeys                  # or: python scripts/run_journeys.py --dry-run  to see the plan
```

For a production-shaped local demo that always serves a fresh embedded-UI build, run:

```bash
python scripts/run_journeys.py --built --fresh-state
```

In built mode, the launcher rebuilds each embedded UI and stops only stale listeners from the
matching demo repo before starting. This prevents an earlier `next start` process from serving
old assets on a reused demo port.

The shells install and run from their own folders (`ui-rm`, `ui-ops`); each has its own README.

## Layout

```
src/journey_portal/    the BFF (hexagonal: domain / ports / adapters / api / cli)
  domain/              pure stdlib: journey, identity, tenant-embed, audit-integrity policy,
                       plus the JOSE encodings, client-assertion minter, CSRF and Doc1 host proof
  ports/               one Protocol per external edge, enumerated in ports/__init__.py __all__;
                       identity is the commons port
  adapters/            local | gcp | platform | onprem, one implementation per port per profile
  api/                 proxy routes + tenant security + reviewer-facing policy/audit views,
                       the published BFF JWK set, and the Doc1 Mode 5 grant routes
config/journeys.yaml   the journeys (config, not code)
ui-rm/                 RM Journey shell (React / Next.js)
ui-ops/                Ops Journey shell (Angular)
scripts/run_journeys.py  the one-command launcher
eval/                  offline gate: journey, identity, routing and tenant-policy isolation
docs/                  adoption, FAQ, embedding/identity, runbook, migration and the
                       named-deployment dossier
infra/terraform/       complete IAP edge, shells, embedded services and regional controls
```

The production Terraform selects the `platform` profile: every content-free portal access and
tenant-policy event is sent synchronously to Hrz5 `/v1/audit` with an audience-bound workload
identity token. The payload contains only keyed actor/tenant references and bounded route metadata.

## Where this sits in the catalog

`Hrz9`, a P1 platform shell. It depends on the apps it composes (`Doc1`, `Doc2`, `Doc3`, `Doc4`,
`Doc5`, `Rsk1`, `Hrz7`) but they do not depend on it: it is an additive host, so nothing else
changes. The RM journey includes both onboarding systems: Doc1 CDD/source-of-wealth and Doc5
loan/mortgage document intelligence. It
is the embeddable-micro-frontend claim made demonstrable. Build standard, conventions, and the
hexagon are shared with the rest of the catalog (`SPEC.md`, `ARCHITECTURE.md`, `COMPLIANCE.md`).

Adopters start with [`docs/ADOPTING.md`](docs/ADOPTING.md), the
[`docs/faq/`](docs/faq/index.md), and the production [`docs/runbook.md`](docs/runbook.md).
The named deployment input contract starts in [`.env.example`](.env.example) and
[`.env.secrets.example`](.env.secrets.example); `make deployment-check` refuses placeholders.
