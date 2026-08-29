# `e2e/`: the RM journey, driven in a real browser

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
# The local stack, PRODUCTION-SHAPED. --built is not a preference:
PORTAL_PROFILE=local python scripts/run_journeys.py --journey rm --built

make e2e-local        # needs the launcher above already up
make e2e-gcp          # needs gcloud, and the three inputs below
make e2e-pair         # F4: assert the two agree. Needs both runs above to have happened.
```

**`--built`, not the default dev launch.** Behind the portal's reverse proxy a `next dev` embed
never hydrates: the markup is served, the chunks load, React never attaches, and the console sits
on "Connecting to the CDD agent" until the step times out — against an API answering `200` in
milliseconds. `next start` behaves correctly, and it is also the shape the deployment runs, so the
laptop leg of a pair should not be measuring a dev server anyway. This cost an hour once; it is
written here rather than in the runbook that used to carry the warning.

**`PORTAL_PROFILE=local` must be set deliberately.** The launcher refuses to inherit it, so a run
that has not chosen a profile stops instead of silently taking the seeded wildcard tenant embed
policy. That is the three-state rule working, not a fault.

`e2e-gcp` names its deployment explicitly. Three settings, read from the environment and
deliberately absent from every `.env` in this repo, because each one names ONE live deployment and
a default here would point a green run at the wrong origin the day that deployment is rebuilt:

| Setting | What it is | Where the value comes from |
|---|---|---|
| `PORTAL_E2E_BASE_URL` | the deployed RM origin, https, no trailing path | the deployment record for the installation being driven |
| `PORTAL_E2E_IAP_AUDIENCE` | the IAP OAuth client id the edge accepts as `aud` | Terraform computes it during the apply; operators must not guess it (`docs/named-deployment-dossier.md`, section 2) |
| `PORTAL_E2E_SERVICE_ACCOUNT` | the dedicated e2e service account to impersonate | the deployment's IAM record; it holds `roles/iap.httpsResourceAccessor` and nothing else |

`targets.py` refuses an unset one and refuses a set-but-empty one, so a half-configured run fails
rather than quietly taking the laptop path.

Artifacts land in `e2e/out/<target>/`: a screenshot per step, `evidence.json` naming what was
asserted, and `dossier.json`, the deterministic artifact captured from the console's own
`POST /v1/cdd`. They are gitignored: evidence is produced, never committed.

**A pair is a pair of RUNS.** `rm_journey` deletes `dossier.json` and `evidence.json` before it
starts and records the dossier's digest in the evidence beside it, so a run that fails leaves
nothing pairable and `make e2e-pair` refuses an artifact the run record next to it does not vouch
for. Both mechanisms exist because neither did: a failed run used to leave the previous run's
dossier in place, and the pair then reported PASS for a run that never happened. Each side's
origin and timestamps are copied into `out/pair/comparison.json`, so a stale pairing can no longer
read like a fresh one.

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
decision, a factor that fired on one profile and not the other, a different estimated value for a
source of wealth, a screen that one profile silently did not run, or a claim grounded in a
different kind of source. One tolerance runs in a single direction: the laptop may report that it
did not search the public web, and the managed profile may not, so a managed search that
disappears is a divergence rather than a reduction.

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

Its fixture is held to the API's own response models, imported from the sibling `cdd-sow-research`
tree rather than copied. That is not ceremony: the fixture used to carry fields no response has
ever sent, the comparator read those same invented fields, and so every "can fail" case here
passed while the live comparison compared nothing at all.

**`e2e-local`, `e2e-gcp` and `e2e-pair` are all operator-invoked, and none of them gate CI.** An
earlier version of this file claimed `e2e-local` joined the gate. It did not, and saying so was
the same defect class the catalog's own re-audit hunts: evidence that is cited but does not run.
`e2e-local` needs the whole journey stack up and a browser; `e2e-gcp` needs an IAP credential CI
does not hold; and a gate that depends on a live origin fails for reasons that have nothing to do
with the commit under test.
