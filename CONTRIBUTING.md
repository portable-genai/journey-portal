# Contributing

## The hard gate (green before anything lands)

```bash
make check     # ruff check + ruff format --check + mypy src + pytest -m 'not integration' + eval
```

SDK-free: CI installs only the dev toolchain (no Google Cloud SDK) and the gate must pass, which is
the portability proof. The shells are typechecked separately:

```bash
cd ui-rm  && npm ci && npm run lint     # tsc --noEmit
cd ui-ops && npm ci && npm run lint     # tsc -p tsconfig.app.json --noEmit
```

## Conventions (enforced by the gate)

- **Keep `domain/` pure stdlib.** No FastAPI, httpx, pydantic, or `google-*` under `domain/`.
  Everything external is a port.
- **GCP imports are lazy.** In `adapters/gcp/*` every Google import lives inside a method, so the
  `local`/`onprem` profiles import with no SDK installed.
- **One adapter constructor:** `def __init__(self, settings: Settings)`. The dotted `module:Class`
  binding in `config.py` is the contract. Every port needs a `local` and an `onprem` binding (the
  parity test asserts it).
- **The identity invariant is load-bearing.** Do not add a code path where a browser-supplied
  identity header can reach an embedded app. Changes to `domain/identity_injection.py` must keep
  `tests/test_identity_injection.py` and the `identity_isolation` eval metric green, and the
  not-falsely-green test must still prove the metric can fail.
- **Fail closed.** No path where the no-auth local profile binds off loopback by default, CORS falls
  back to `*`, or an unset secret is treated as authenticated (the commons `netdefaults` enforce
  this; do not work around them).
- **Journeys are config.** Add or recompose journeys in `config/journeys.yaml`, not in code.
- **Docs style:** no em-dashes in `.md`/`.html` files, commit messages, or PR bodies. Use colons,
  commas, or parentheses. Obviously fictional identifiers only.

## Adding an embedded app to a journey

1. Add the app under `apps:` in `config/journeys.yaml` (label + `ui_upstream` + `api_upstream`, with
   `${ENV:-default}` interpolation), and add its id to a journey's `apps:` list.
2. Add its id -> repo folder to `_APP_REPOS` in `scripts/run_journeys.py` so the launcher can start
   it.
3. The app must be an `embeddable-secure-ui` consumer (embed mode + basePath + a server-verified
   `IdentityPort`); every built P1 app already is.

## Adding a port or adapter

For a new port, add one `@runtime_checkable` Protocol under `ports/`, re-export it from
`ports/__init__.py`, add an exact binding for every runtime profile in `config.py`, expose it from
the container, and add constructor/conformance and behavior tests. For a new adapter to an
existing port, keep `Adapter(settings)`, use lazy managed-SDK imports, update the exact binding,
and prove fail-closed behavior. The parity test must fail when any profile binding is missing.

## Git

Use a feature branch and a green reviewable commit. Commits are authored solely by the user (no
co-author trailers). Synthetic, obviously fictional data only.
