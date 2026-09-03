"""Where a journey is being driven, and how the browser is allowed to reach it.

Three states, never two, on every setting this module reads: unset is not the same answer as set
to nothing. A deployment template that renders `PORTAL_E2E_TARGET=` blank must fail loudly rather
than silently taking the laptop path and reporting a green cloud demo.

One portal serves several journeys, and a journey is served by its own SHELL on its own origin
(the React shell serves every journey but `ops`, which keeps the Angular one). ``shells()``
resolves journey -> origin for whichever target is being driven, so a spec that walks the whole
catalog does not have to know how a laptop assigns ports or how a deployment names hosts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

LOCAL = "local"
GCP = "gcp"
_TARGETS = (LOCAL, GCP)

#: The RM shell reverse-proxies the BFF in dev, so the browser only ever sees this one origin.
_DEFAULT_LOCAL_ORIGIN = "http://localhost:3000"

#: The ports ``scripts/run_journeys.py`` assigns each journey's shell on a laptop. Kept in step
#: with that launcher deliberately: it is the only thing that decides where a local shell listens,
#: so a coverage run that guessed differently would report "not deployed" for a shell that is
#: running two ports away. The `rm` entry is absent on purpose -- that origin is
#: ``PORTAL_E2E_BASE_URL``, which both specs already share.
_LOCAL_SHELL_PORTS: dict[str, int] = {"mkt": 3001, "gov": 3002, "svc": 3003, "ops": 4200}

#: The journey a shell is EXPECTED to render, per shell origin, is named by the operator rather
#: than discovered, because "this origin serves the ops journey" is exactly the claim under test:
#: the React shell falls back to the first journey in the catalog when the one it was built for
#: is absent, so a shell that has lost its journey still renders a perfectly healthy page.
_SHELL_ORIGIN_ENV = "PORTAL_E2E_SHELL_{key}_BASE_URL"
_SHELLS_ENV = "PORTAL_E2E_SHELLS"


class TargetError(RuntimeError):
    """The run was not told enough to be honest about what it is proving."""


def setting(name: str) -> str | None:
    """Return None when unset and raise when set-and-empty. The middle state is the point."""

    if name not in os.environ:
        return None
    value = os.environ[name].strip()
    if not value:
        raise TargetError(
            f"{name} is set to an empty value. Leave it unset to take the documented default, or "
            f"name the value you mean; a blank setting is how a rendered template silently "
            f"redirects a demo at the wrong origin."
        )
    return value


@dataclass(frozen=True)
class Target:
    name: str
    base_url: str
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def is_cloud(self) -> bool:
        return self.name == GCP


@dataclass(frozen=True)
class Shell:
    """One journey's shell: the origin to point a browser at, and the journey it must render."""

    journey: str
    origin: str
    #: How this origin was decided, copied into the evidence so a reader of a coverage report
    #: can tell a named deployment origin from a laptop port convention without re-deriving it.
    source: str


def _requested_shells(catalog_journeys: tuple[str, ...]) -> tuple[str, ...]:
    """The journeys this run is asked to drive: every one the target serves, or a named subset."""

    named = setting(_SHELLS_ENV)
    if named is None:
        return catalog_journeys
    requested = tuple(part.strip() for part in named.split(",") if part.strip())
    if not requested:
        raise TargetError(f"{_SHELLS_ENV} names no journey; unset it to drive every one")
    unknown = [key for key in requested if key not in catalog_journeys]
    if unknown:
        raise TargetError(
            f"{_SHELLS_ENV} names {', '.join(unknown)}, which this target's catalog does not "
            f"serve. It serves: {', '.join(catalog_journeys)}. A journey the portal does not "
            "serve cannot be driven, and pretending otherwise is how a coverage run reports a "
            "shell it never opened."
        )
    return requested


def shells(
    target: Target, catalog_journeys: tuple[str, ...]
) -> tuple[tuple[Shell, ...], tuple[str, ...]]:
    """Resolve (drivable shells, journeys with no origin) for this target.

    The second half is the point. A journey the portal serves but that this run has no origin for
    is REPORTED as undriven rather than skipped: on a deployment that publishes one host per
    persona, "we never opened the ops shell" and "the ops shell is fine" must not print the same.
    """

    drivable: list[Shell] = []
    undriven: list[str] = []
    for journey in _requested_shells(catalog_journeys):
        explicit = setting(_SHELL_ORIGIN_ENV.format(key=journey.upper()))
        if explicit is not None:
            drivable.append(Shell(journey=journey, origin=explicit.rstrip("/"), source="named"))
            continue
        if journey == "rm":
            # The existing contract: PORTAL_E2E_BASE_URL is the RM origin on both targets.
            drivable.append(Shell(journey=journey, origin=target.base_url, source="base-url"))
            continue
        port = _LOCAL_SHELL_PORTS.get(journey)
        if target.name == LOCAL and port is not None:
            drivable.append(
                Shell(journey=journey, origin=f"http://localhost:{port}", source="launcher-port")
            )
            continue
        undriven.append(journey)
    if not drivable:
        raise TargetError(
            "no shell origin could be resolved for any journey this target serves. Name one with "
            + _SHELL_ORIGIN_ENV.format(key="<JOURNEY>")
        )
    return tuple(drivable), tuple(undriven)


def _mint_iap_token(service_account: str, audience: str) -> str:
    """Mint an IAP-accepted identity token without any human credential.

    IAP accepts a bearer OIDC token whose ``aud`` is the IAP OAuth client id. ``gcloud`` will mint
    exactly that for a service account it is allowed to impersonate, so nothing here types,
    stores or sees a password, and the same command works from a laptop and from an operator's
    shell.
    """

    if shutil.which("gcloud") is None:
        raise TargetError("the gcp target needs the gcloud CLI on PATH to mint an IAP token")
    completed = subprocess.run(
        [
            "gcloud",
            "auth",
            "print-identity-token",
            f"--impersonate-service-account={service_account}",
            f"--audiences={audience}",
            "--include-email",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    token = completed.stdout.strip()
    if completed.returncode != 0 or not token:
        raise TargetError(
            "could not mint an IAP identity token for "
            f"{service_account}: {completed.stderr.strip()[:400]}"
        )
    return token


def resolve() -> Target:
    """Resolve the target from the environment, refusing anything ambiguous."""

    name = setting("PORTAL_E2E_TARGET")
    if name is None:
        raise TargetError(
            "PORTAL_E2E_TARGET is unset. Name the target explicitly: "
            f"one of {', '.join(_TARGETS)}. There is deliberately no default, because the two "
            "targets prove different claims and a wrong guess reports the wrong one as green."
        )
    if name not in _TARGETS:
        raise TargetError(f"PORTAL_E2E_TARGET must be one of {_TARGETS}, not {name!r}")

    if name == LOCAL:
        return Target(name=LOCAL, base_url=setting("PORTAL_E2E_BASE_URL") or _DEFAULT_LOCAL_ORIGIN)

    base_url = setting("PORTAL_E2E_BASE_URL")
    audience = setting("PORTAL_E2E_IAP_AUDIENCE")
    service_account = setting("PORTAL_E2E_SERVICE_ACCOUNT")
    missing = [
        variable
        for variable, value in (
            ("PORTAL_E2E_BASE_URL", base_url),
            ("PORTAL_E2E_IAP_AUDIENCE", audience),
            ("PORTAL_E2E_SERVICE_ACCOUNT", service_account),
        )
        if value is None
    ]
    if missing:
        raise TargetError(
            "the gcp target names a real deployment, so nothing about it is defaulted. Missing: "
            + ", ".join(missing)
        )
    assert base_url is not None and audience is not None and service_account is not None
    if not base_url.startswith("https://"):
        raise TargetError(
            f"PORTAL_E2E_BASE_URL must be https for the cloud target, got {base_url!r}: an IAP "
            "token handed to a plaintext origin is a credential on the wire."
        )
    token = _mint_iap_token(service_account, audience)
    return Target(
        name=GCP, base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}
    )
