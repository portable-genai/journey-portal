# `e2e/` — the RM journey, driven in a real browser

One suite, two targets. The same assertions run against the portal on a laptop and against the
portal deployed on Google Cloud behind Identity-Aware Proxy, because a demo that only works in
one of those proves the wrong thing.

| Target | Origin | Identity |
|---|---|---|
| `local` | the RM shell on `http://localhost:3000`, which reverse-proxies the BFF | the portal's own persona cookie; no cloud, no credential |
| `gcp` | the deployed RM origin | an IAP-accepted OIDC identity token, minted by `gcloud` for a dedicated service account |

Both targets see the SAME paths (`/`, `/v1/*`, `/agent/*`), because the deployed load balancer
routes those prefixes to the BFF and the local shell rewrites them to it. That is what makes one
spec able to describe both, and it is also the mode-1 same-origin embedding claim under test.

## Running it

```bash
make e2e-local        # needs `make journeys --journey rm` (or `python scripts/run_journeys.py --journey rm`) already up
make e2e-gcp          # needs gcloud, and access to the deployment project
```

Artifacts land in `e2e/out/<target>/`: a screenshot per step and `evidence.json` naming what was
asserted. They are gitignored — evidence is produced, never committed.

## Why the GCP target never types a password

IAP accepts a bearer OIDC token whose audience is the IAP OAuth client. The suite mints one with
`gcloud auth print-identity-token --impersonate-service-account=<sa> --audiences=<client-id>`, so
no human credential and no browser sign-in is involved and the run is repeatable from CI or a
laptop. The service account holds `roles/iap.httpsResourceAccessor` and nothing else.

## What gates CI, and what does not

`e2e-local` is offline and joins the repository gate. `e2e-gcp` is deliberately operator-invoked:
CI holds no IAP credential, and a gate that depends on a live origin fails for reasons that have
nothing to do with the commit under test.
