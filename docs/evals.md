# How the journey portal is evaluated

Read this page if you decide what this portal is allowed to compose, route or forward. The
metrics, the bars and the corpus below are generated from the artifacts that actually gate the
build, so they cannot drift from what runs: `make evals-doc-check` fails the build when this page
and those artifacts disagree.

## How to run it

```sh
make eval              # offline, no credentials
make evals-doc-check   # this page is still true
```

`make check` runs both on every change.

## This is not an agent, and the eval knows it

Nothing here generates text. The portal composes journeys, routes requests, forwards a verified
identity and emits audit events, and every one of those is deterministic. So the eval harness is
re-purposed to score **security and composition invariants**, which is the right reading of an
eval for a trust boundary: what a portal owes is not "a good answer" but "the same refusal, every
time".

Two consequences, and both are deliberate:

- **Narrative quality (F4) and real-model behaviour (F5) are `n/a` here**, and are recorded as
  `n/a` rather than left blank. A blank row reads as an omission; `n/a` with a reason reads as a
  decision. There is no model in this service to judge and no model output to replay.
- **Every bar is 1.0.** The four bars that used to read 0.99 were arithmetically identical to
  1.0 over corpora of four and six cases, and they are also what the properties mean: a trust
  boundary that holds 99% of the time is a boundary with a hole in it.

## What is measured, and against what bar

Every bar below lives in `eval/rubrics/*.yaml` next to the argument for it, and the
runner reads it from there. There is no dict of thresholds in the runner any more: a
metric scored with no reviewed bar fails the build, and so does a bar that names no
metric, which is the direction that rots quietly because it rots toward looking well
governed.

The third column is the denominator rule, and it applies only where a score is a
FRACTION over scored positives: such a threshold `t` tolerates a single miss only over
at least `1/(1-t)` of them. `all or nothing` marks a bar that already asks for no
headroom, so a bigger corpus would not change what it means. Each rubric declares which
it is rather than the rule being guessed from the number.

| Metric | Bar | Denominator | What it measures |
|---|---|---|---|
| `csrf_token_integrity` | 1 | all or nothing | A CSRF token verifies for exactly the session and action it was minted for, and is refused when it comes from another session, names another action, has expired, was tampered with, or is absent. |
| `host_proof_integrity` | 1 | all or nothing | Only an exact same-origin script call from the reviewed origin is accepted; a look-alike origin, a cross-site or unlabelled call, and a navigation or document request are all refused. |
| `identity_isolation` | 1 | all or nothing | The identity the portal forwards upstream is the one it verified, and no spoofed inbound header survives sanitisation. |
| `journey_integrity` | 1 | all or nothing | The journey-catalogue builder accepts exactly the configurations a reviewer marked valid and refuses exactly the ones marked invalid. |
| `observability_audit_isolation` | 1 | all or nothing | The observability-audit event carries no cross-tenant identity and no raw subject. |
| `routing_correctness` | 1 | all or nothing | The API and UI targets the portal derives for a mounted application are exactly the ones the golden case names. |
| `tenant_policy_isolation` | 1 | all or nothing | One tenant's embed policy governs one tenant's frames, and a policy from another tenant is never applied. |

Scored over 35 golden cases.

## What is exercised

35 golden cases in `eval/datasets/golden_journeys.jsonl`, each carrying its
own `kind`. A row whose kind no metric scores is REFUSED rather than counted: it would
otherwise still count toward `n_examples`, so the report would look evidenced while the
metric that should have selected it selected nothing.

| Metric | Case kind | Cases |
|---|---|---|
| `csrf_token_integrity` | `csrf` | 7 |
| `host_proof_integrity` | `host-proof` | 8 |
| `identity_isolation` | `identity` | 4 |
| `journey_integrity` | `config` | 6 |
| `observability_audit_isolation` | `observability-audit` | 2 |
| `routing_correctness` | `routing` | 4 |
| `tenant_policy_isolation` | `tenant-policy` | 4 |

## The two invariants this gate was missing

The CSRF token and the browser-provenance proof are the two things standing between a hostile
page and a grant made in a signed-in user's name, and both were covered only by unit tests. A
unit test can be deleted in the same commit as the thing it guards, and a promotion that
regressed either should not be certifiable. They are now scored kinds, through the shipped
`verify_csrf_token` and `assess_browser_provenance` rather than a re-implementation.

Both corpora carry refusals as well as acceptances, and the refusals are the interesting half: a
token from another session, for another action, expired, tampered with or absent; a look-alike
origin (`portal.example.attacker.test`), an unlabelled fetch site, a same-site rather than
same-origin call, and a navigation request. A one-directional corpus would score a verifier that
accepts everything at a perfect 1.000, which is indistinguishable from having no check at all.

## How a metric is prevented from being decoration

1. **The bars are read from the rubrics, in both directions.** This repository had no rubric
   directory at all: every bar was an unlabelled module constant, which is exactly what practice
   E1 asks a repository not to do. `assert_covers` now fails the build when a metric has no
   reviewed bar AND when a bar names no metric.
2. **A case kind no metric scores is refused.** Such a row still counts toward `n_examples`, so
   the report would look evidenced while the metric that should have selected it selected nothing.
3. **An empty selection scores 0.0, never 1.0**, and `_warn_unmeasured` names the metric on
   stderr so the zero explains itself.
4. **The promotion bundle default is a name the authority registers.** It used to be `"local"`,
   which the authority does not register, so an unset `PORTAL_BUNDLE_ID` sent a name that fails
   closed on `UnknownMetricError`: not a loose gate, no gate. Nothing reported it, because the
   offline smoke path never touches that line.

## What is NOT measured here

- **Narrative quality and real-model behaviour**: `n/a`, as above.
- **The browser tier's own behaviour.** These metrics score the portal's Python surface. The
  console's CSP, hydration and provenance checks are a separate gate (`make ui-check`).
- **Production traffic.** Everything here is a golden set. Nothing samples live requests.
