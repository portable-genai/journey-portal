# RM Journey shell (React / Next.js)

The thin **React** host for the RM Journey. It renders a persona switcher and a tabbed set of
same-origin iframes over the portal BFF, and nothing else: identity, reverse-proxying and journey
config all live in the BFF (`../src/journey_portal`). Its small size is deliberate and is the
point of the two-framework demo (this shell and the Angular `../ui-ops` shell are two host
frameworks over one shared BFF).

## How it embeds (mode 1, same-origin)

`next.config.mjs` reverse-proxies the BFF:

- `/apps/*` -> the BFF (embedded app UIs and their APIs)
- `/v1/*` -> the BFF (journey catalog, personas, whoami, persona selection)

The browser only ever sees this shell's origin, so every embedded `<iframe src="/apps/<id>/">` is
first-party. No CORS, no third-party cookies. This is the reverse-proxy role a production edge /
CDN plays; in dev the Next dev server plays it.

## Run

```bash
cp .env.local.example .env.local     # PORTAL_BFF_ORIGIN, NEXT_PUBLIC_JOURNEY=rm
npm install
npm run dev                          # http://localhost:3000  (needs the BFF on :8110)
npm run lint                         # tsc --noEmit
```

The whole demo (BFF + every embedded app backend and UI + this shell) comes up with one command
from the repo root: `make journeys` (see `../scripts/run_journeys.py`).
