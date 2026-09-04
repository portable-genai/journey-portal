# journey-portal

The shared working agreement is [`.github/AGENTS.md`](https://github.com/portable-genai/.github/blob/main/AGENTS.md).
It carries the architecture rules, the gate contract, the fleet invariants, the
falsification discipline, versions and house style, and it holds in every repository
here. Read it first. This file carries only what is specific to this one.

## What this is

`journey-portal`, the Journey Portal Shell: a persona-journey host that composes the built P1 app UIs into
one UI per user (an RM Journey and an Ops Journey) via same-origin reverse-proxy embedding. A
FastAPI **BFF** owns the reverse proxy, the journey config, and the identity trust boundary; two
thin UI shells consume it in two frameworks on purpose (`ui-rm` React/Next.js, `ui-ops` Angular).
It is the catalog's first runnable proof of the embeddable-micro-frontend claim.

## Commands

`PORTAL_PROFILE=local` (SDK-free offline) is what the Makefile exports for you; there is no
default and an unset variable refuses to serve. You almost never need Google Cloud installed.

```bash
pip install -e ".[dev]"        # core + dev tools; no google-cloud-*
make check                     # the hard gate: ruff + ruff format --check + mypy src + pytest + eval
make demo                      # offline audit view (journeys + identity boundary) -> scripts/out/
make run-api                   # BFF on :8110
make journeys                  # the whole live demo (needs the sibling repos + npm installs)
```

`make check` expands to
`ruff check src tests eval && ruff format --check src tests eval && mypy src &&
pytest -m 'not integration' && python eval/run_eval.py`.

## Architecture: the hexagon

Pure-stdlib domain core (`domain/`: journey `catalog` + `identity_injection`), typed `ports/`
(`UpstreamClientPort`; identity is the commons `hex_service_kit.identity.IdentityPort`), swappable
`adapters/{local,gcp,onprem}/`, a profile-driven `config.py` container, and a FastAPI `api/`. One
adapter per port per profile; `local` and `onprem` import with no cloud SDK (the parity test
asserts it).

## Conventions you must follow

- **The identity invariant is load-bearing:** a browser-asserted identity must never reach an
  embedded app. `domain/identity_injection.py` strips the client-spoofable identity headers and
  injects the portal-verified identity; keep `tests/test_identity_injection.py` and the
  `identity_isolation` eval metric green (and the not-falsely-green test proving it can fail).
- **Journeys are config** (`config/journeys.yaml`), not code.
- **Fail closed** (loopback bind for no-auth local; CORS never `*`); the commons `netdefaults`
  enforce this.
- **Adopt the commons, do not copy them** (`hex-service-kit`, `agent-eval-kit`, `pii-kit`, pinned
  by tag).
- **Commits** are authored solely by the user (no co-author trailers); push direct to main once
  the gate is green.

## The two shells

`ui-rm` (React/Next.js) and `ui-ops` (Angular) are deliberately thin and share no UI code; both
compose the same BFF. Not in the Python gate; typecheck each with `npm run lint`. The two-framework
split is the point (host-framework agnosticism), so keep both in step when the BFF contract changes.
