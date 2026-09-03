.PHONY: test-managed help install lint format typecheck test eval check run-api run-rm run-ops journeys demo demo-selftest demo-browser-selftest e2e-local e2e-gcp e2e-apps-local e2e-apps-gcp e2e-pair portability deployment-check deployment-render docker-build docker-build-all tf-validate clean lock

PY ?= python3
PY := $(if $(wildcard .venv/bin/python),.venv/bin/python,$(PY))
PORT ?= 8110
API_HOST ?= 127.0.0.1
export PORTAL_PROFILE ?= local

help: ## List targets.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Editable install with the SDK-free dev toolchain.
	pip install -r requirements-dev.lock
	pip install --no-deps -e .

# `scripts` as a DIRECTORY, not an enumerated list. The list named eight of the fifteen files
# in it, so seven were unlinted, and the default for a newly added script was to be unchecked
# forever. That is the wrong default for a gate: a file should have to be excluded on purpose,
# never included on purpose. The gap was found on 2026-08-29 the expensive way, when a
# stray restore reverted `scripts/demo_walkthrough.py` to an unformatted snapshot and
# `make lint` reported success, because that file was one of the seven. All seven passed
# unchanged the moment they were covered, which is what made the list look harmless.
# `e2e` joined the list on 2026-09-03, for the reason the paragraph above gives: it was outside
# it, so the browser specs that produce this repository's deployment evidence were the one body
# of code the gate never checked. They were clean when added, which is what made the gap
# invisible -- an unchecked path and a passing one look identical from here.
LINT_PATHS = src tests eval scripts e2e ui-rm/static_server.py ui-ops/static_server.py

lint: ## ruff check + format check.
	ruff check $(LINT_PATHS)
	ruff format --check $(LINT_PATHS)

format: ## Auto-format and fix.
	ruff format src tests eval
	ruff check --fix src tests eval

typecheck: ## Static type check (mypy strict).
	mypy src

# The presenter runners' own suites live beside them under scripts/ and sit outside
# `testpaths`, so naming them here is what makes them run at all. A test the gate never
# invokes cannot catch anything, however much it asserts. `tests` is named explicitly
# alongside them because any path argument replaces `testpaths` entirely, which would
# quietly reduce this target to the two scripts suites.
test: ## Offline pytest suite (local profile).
	pytest -m 'not integration' tests scripts/test_demo_walkthrough.py scripts/test_run_journeys.py

eval: ## Offline evaluation gate (smoke; exit non-zero on fail).
	$(PY) eval/run_eval.py

# The full offline hard gate (what CI runs).
check: lint typecheck test eval ## Lint + typecheck + test + eval.

run-api: ## Serve the portal BFF locally (loopback, local profile).
	uvicorn journey_portal.api.app:app --host $(API_HOST) --port $(PORT) --reload

run-rm: ## Dev-serve the React (Next.js) RM shell (needs the BFF running).
	cd ui-rm && npm run dev

run-ops: ## Dev-serve the Angular Ops shell (needs the BFF running).
	cd ui-ops && npm start

journeys: ## Launch the whole demo: every journey app (backend + UI) behind the portal.
	$(PY) scripts/run_journeys.py

demo: ## Render the offline journey + identity-injection audit view to HTML.
	$(PY) scripts/portal_demo.py

demo-selftest: ## Assert the live BFF route and identity evidence offline.
	$(PY) scripts/demo_selftest.py

demo-browser-selftest: ## Assert both production-built shells in headless Chromium.
	$(PY) scripts/demo_browser_selftest.py

e2e-local: ## Drive the RM journey in a real browser on this machine (needs run_journeys.py --built; see e2e/README.md).
	PORTAL_E2E_TARGET=local $(PY) e2e/rm_journey.py

e2e-gcp: ## Drive the SAME journey against the deployment (needs gcloud and the three PORTAL_E2E_* inputs; see e2e/README.md).
	@# The three are read from the environment and from no .env file: none of them is recorded in
	@# .env, .env.example or .env.secrets, and this help text used to send an operator there to
	@# look. Each names one specific live deployment, so a default would be the wrong deployment
	@# the moment one is rebuilt. e2e/README.md says where each value comes from.
	PORTAL_E2E_TARGET=gcp \
	PORTAL_E2E_BASE_URL=$${PORTAL_E2E_BASE_URL:?name the deployed RM origin} \
	PORTAL_E2E_IAP_AUDIENCE=$${PORTAL_E2E_IAP_AUDIENCE:?name the IAP OAuth client id} \
	PORTAL_E2E_SERVICE_ACCOUNT=$${PORTAL_E2E_SERVICE_ACCOUNT:?name the e2e service account} \
	$(PY) e2e/rm_journey.py

e2e-apps-local: ## Embed and check EVERY app this laptop serves, across every journey shell.
	PORTAL_E2E_TARGET=local $(PY) e2e/app_coverage.py

e2e-apps-gcp: ## The same sweep against the deployment. Drives the shells it is given origins for.
	@# PORTAL_E2E_BASE_URL is the RM origin, as it is for e2e-gcp. Every OTHER journey's shell is
	@# named with PORTAL_E2E_SHELL_<JOURNEY>_BASE_URL (e.g. ..._OPS_BASE_URL), and a journey the
	@# deployment serves but this run has no origin for is REPORTED as never opened rather than
	@# passed over: on a deployment publishing one host per persona, those two must not print the
	@# same. e2e/README.md says where each value comes from.
	PORTAL_E2E_TARGET=gcp \
	PORTAL_E2E_BASE_URL=$${PORTAL_E2E_BASE_URL:?name the deployed RM origin} \
	PORTAL_E2E_IAP_AUDIENCE=$${PORTAL_E2E_IAP_AUDIENCE:?name the IAP OAuth client id} \
	PORTAL_E2E_SERVICE_ACCOUNT=$${PORTAL_E2E_SERVICE_ACCOUNT:?name the e2e service account} \
	$(PY) e2e/app_coverage.py

test-managed: ## Managed trust-boundary suite against a NAMED deployment (needs gcloud).
	@# Three states, never two: PORTAL_MANAGED_TEST_BASE_URL unset skips (so the offline gate is
	@# unaffected), named-and-reachable runs, named-and-unusable FAILS. What it asserts are
	@# properties of the HOP, which no offline fixture can carry: the reserved x-goog namespace,
	@# the injected hop credential, and the frontend-answered /healthz.
	$(PY) -m pytest -m integration -q tests/test_managed_trust_boundary.py -rs

e2e-pair: ## F4: assert the laptop and the deployment AGREE (needs both runs to have happened).
	$(PY) e2e/pair_report.py

portability: ## Execute bounded channel and runtime portability evidence.
	$(PY) scripts/portability_demo.py

deployment-check: ## Fail closed unless .env and .env.secrets are production-ready.
	$(PY) scripts/deployment_config.py check

deployment-render: ## Validate and render ignored, non-secret Terraform inputs.
	$(PY) scripts/deployment_config.py render

docker-build: ## Build the serving image (BFF).
	docker build -t journey-portal:dev .

docker-build-all: ## Build BFF, RM and Ops immutable-image inputs.
	docker build -t journey-portal:dev .
	docker build -t hrz-journey-rm:dev ui-rm
	docker build -t hrz-journey-ops:dev ui-ops

tf-validate: ## Format and validate the reusable deployment stack offline.
	terraform -chdir=infra/terraform fmt -check -recursive
	terraform -chdir=infra/terraform init -backend=false
	terraform -chdir=infra/terraform validate
	terraform -chdir=infra/terraform test

lock: ## Recompile every dependency lockfile with uv (needs network).
	# requirements-build.in is hatchling only: no commons pins, so no header to restore.
	uv pip compile requirements-build.in --python-version 3.12 -o requirements-build.lock
	# Every pyproject-derived lockfile goes through the script, NOT a raw uv line per file.
	# The raw lines destroyed the tag = commit header, and having two of them here and two in
	# the script is how requirements-demo.lock got left a release behind on the commons.
	python3 scripts/lock.py

clean: ## Remove caches and build artifacts.
	rm -rf .mypy_cache .pytest_cache .ruff_cache build dist *.egg-info
