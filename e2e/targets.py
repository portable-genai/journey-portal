"""Where the RM journey is being driven, and how the browser is allowed to reach it.

Three states, never two, on every setting this module reads: unset is not the same answer as set
to nothing. A deployment template that renders `PORTAL_E2E_TARGET=` blank must fail loudly rather
than silently taking the laptop path and reporting a green cloud demo.
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


class TargetError(RuntimeError):
    """The run was not told enough to be honest about what it is proving."""


def _setting(name: str) -> str | None:
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

    name = _setting("PORTAL_E2E_TARGET")
    if name is None:
        raise TargetError(
            "PORTAL_E2E_TARGET is unset. Name the target explicitly: "
            f"one of {', '.join(_TARGETS)}. There is deliberately no default, because the two "
            "targets prove different claims and a wrong guess reports the wrong one as green."
        )
    if name not in _TARGETS:
        raise TargetError(f"PORTAL_E2E_TARGET must be one of {_TARGETS}, not {name!r}")

    if name == LOCAL:
        return Target(name=LOCAL, base_url=_setting("PORTAL_E2E_BASE_URL") or _DEFAULT_LOCAL_ORIGIN)

    base_url = _setting("PORTAL_E2E_BASE_URL")
    audience = _setting("PORTAL_E2E_IAP_AUDIENCE")
    service_account = _setting("PORTAL_E2E_SERVICE_ACCOUNT")
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
