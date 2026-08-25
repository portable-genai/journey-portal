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
make e2e-pair         # F4: assert the two agree. Needs both runs above to have happened.
```

Artifacts land in `e2e/out/<target>/`: a screenshot per step, `evidence.json` naming what was
asserted, and `dossier.json`, the deterministic artifact captured from the console's own
`POST /v1/cdd`. They are gitignored — evidence is produced, never committed.

## Running twice is not a pair

Both targets passing separately is a weaker claim than the one this repository publishes:

> The same input, policy version and evidence produce the same consequential figures, checks,
> escalation reasons and citation relationships in every profile. What changes between a managed
> cloud profile and a laptop is quality, scale and durability, never policy.

`make e2e-pair` is that sentence made executable, and is what practices check F4 scores. It reads
both `dossier.json` files, compares the half that must not move, and **exits non-zero when they
disagree**. `e2e/pairing.py` is the specification: what it compares is policy, and what it exempts
is listed beside the reason it is a declared reduction rather than a divergence.

The exemptions are the interesting half. Narration length, snippet text, retrieval ranking and
per-store document ids are all permitted to differ, because a frontier model and a local one, and
a managed retrieval engine and a local index, are exactly the quality reduction the invariant
allows. What is not permitted is a different risk band, a different score, a different escalation
decision, a screen that one profile silently did not run, or a claim grounded in a different kind
of source.

## Why the GCP target never types a password

IAP accepts a bearer OIDC token whose audience is the IAP OAuth client. The suite mints one with
`gcloud auth print-identity-token --impersonate-service-account=<sa> --audiences=<client-id>`, so
no human credential and no browser sign-in is involved and the run is repeatable from CI or a
laptop. The service account holds `roles/iap.httpsResourceAccessor` and nothing else.

## What gates CI, and what does not

`tests/test_pairing.py` joins the repository gate. It is offline, needs no browser and no
credential, and proves the comparison can FAIL: every class of policy divergence is asserted to be
caught, and every declared reduction is asserted to be tolerated. A comparison observed only green
is indistinguishable from one that asserts nothing.

**`e2e-local`, `e2e-gcp` and `e2e-pair` are all operator-invoked, and none of them gate CI.** An
earlier version of this file claimed `e2e-local` joined the gate. It did not, and saying so was
the same defect class the catalog's own re-audit hunts: evidence that is cited but does not run.
`e2e-local` needs the whole journey stack up and a browser; `e2e-gcp` needs an IAP credential CI
does not hold; and a gate that depends on a live origin fails for reasons that have nothing to do
with the commit under test.
