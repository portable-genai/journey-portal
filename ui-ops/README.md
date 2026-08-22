# Ops Journey shell (Angular)

The thin **Angular** host for the Ops Journey. It is the deliberate twin of the React `../ui-rm`
shell: the same persona switcher and the same tabbed set of same-origin iframes over the same
portal BFF, written in a second framework. That is the whole point - the agents drop into whatever
stack the bank already runs, and the two shells share ZERO UI code yet compose the identical
portal, because everything load-bearing (identity, reverse-proxy, journey config) lives in the BFF
(`../src/journey_portal`), not the shell.

Angular is the useful second case precisely because it has **no server tier**: in dev it is
`ng serve` plus `proxy.conf.json`; in production it is static files behind a reverse proxy. Building
it exercises the generic (non-Next.js) host integration path end to end.

## How it embeds (mode 1, same-origin)

`proxy.conf.json` reverse-proxies the BFF for `/apps/*` (embedded app UIs and their APIs) and
`/v1/*` (the portal endpoints). The browser only ever sees this shell's origin (`localhost:4200`),
so every embedded `<iframe src="/apps/<id>/">` is first-party: no CORS, no third-party cookies.

## Run

```bash
npm install
npm start                 # ng serve on http://localhost:4200 (needs the BFF on :8110)
npm run lint              # tsc -p tsconfig.app.json --noEmit
```

The whole demo (BFF + every embedded app + both shells) comes up with one command from the repo
root: `make journeys` (see `../scripts/run_journeys.py`).
