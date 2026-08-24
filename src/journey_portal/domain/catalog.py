"""The journey catalog: build it from raw config, validate it, resolve apps and journeys.

Pure stdlib and deterministic: the same config mapping always yields the same catalog, and every
invariant a malformed config could violate is checked once at load (fail fast) rather than at
request time. The reverse-proxy target-URL builders live here too so they are unit-testable
without a running server.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .errors import JourneyConfigError, UnknownApp, UnknownJourney
from .models import AppMount, Journey

_APP_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
_HTTP_SCHEMES = ("http://", "https://")
_MOUNT_PATH = re.compile(r"^/[a-z0-9][a-z0-9/-]{0,126}$")


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JourneyConfigError(f"{where} must be a non-empty string")
    return value.strip()


def _require_upstream(value: Any, where: str) -> str:
    text = _require_str(value, where)
    if not text.startswith(_HTTP_SCHEMES):
        raise JourneyConfigError(f"{where} must start with http:// or https:// (got {text!r})")
    parsed = urlsplit(text)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise JourneyConfigError(
            f"{where} must be an origin/path without credentials, query, or fragment"
        )
    return text.rstrip("/")


def validate_secure_upstream(url: str) -> None:
    """Reject plaintext managed-profile targets before a request can leave the portal."""
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise JourneyConfigError(f"managed-profile upstream must use https: {url!r}")


def _optional_mount_path(value: Any, where: str) -> str | None:
    if value is None:
        return None
    text = _require_str(value, where).rstrip("/")
    if not _MOUNT_PATH.fullmatch(text) or "//" in text or "/.." in text:
        raise JourneyConfigError(f"{where} must be a canonical absolute path")
    return text


@dataclass(frozen=True, slots=True)
class JourneyCatalog:
    """The validated set of mounted apps and the journeys composed from them."""

    apps: Mapping[str, AppMount]
    journeys: Mapping[str, Journey]

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any], *, only_apps: frozenset[str] | None = None
    ) -> JourneyCatalog:
        """Validate a raw config mapping into a catalog, or raise :class:`JourneyConfigError`.

        ``only_apps`` narrows the catalog to the apps a deployment actually mounts. The config
        file is the CATALOGUE of everything the portal knows how to serve; a given installation
        may license or deploy a subset of it.

        Without this, every app in the file is loaded, and the ones that were not deployed keep
        their ``${VAR:-http://127.0.0.1:...}`` local defaults — so a managed deployment fails at
        startup on a loopback upstream that belongs to a journey it never intended to serve.
        Silently DROPPING such apps would be worse than failing, because a genuinely
        misconfigured upstream would disappear instead of being reported, so the deployment
        states which apps it has and anything named here must exist in the file.
        """
        if not isinstance(raw, Mapping):
            raise JourneyConfigError("journeys config must be a mapping")
        raw_apps = raw.get("apps")
        raw_journeys = raw.get("journeys")
        if not isinstance(raw_apps, Mapping) or not raw_apps:
            raise JourneyConfigError("config needs a non-empty 'apps' mapping")
        if not isinstance(raw_journeys, Mapping) or not raw_journeys:
            raise JourneyConfigError("config needs a non-empty 'journeys' mapping")

        if only_apps is not None:
            if not only_apps:
                raise JourneyConfigError("the deployed-app list must name at least one app")
            unknown = sorted(only_apps - set(raw_apps))
            if unknown:
                raise JourneyConfigError(
                    f"deployed apps not present in the journeys config: {', '.join(unknown)}"
                )
            raw_apps = {app_id: spec for app_id, spec in raw_apps.items() if app_id in only_apps}

        apps: dict[str, AppMount] = {}
        for app_id, spec in raw_apps.items():
            if not isinstance(app_id, str) or not _APP_ID.match(app_id):
                raise JourneyConfigError(
                    f"app id {app_id!r} must match {_APP_ID.pattern} (used in a URL path)"
                )
            if not isinstance(spec, Mapping):
                raise JourneyConfigError(f"app {app_id!r} spec must be a mapping")
            apps[app_id] = AppMount(
                app_id=app_id,
                label=_require_str(spec.get("label"), f"app {app_id!r} label"),
                ui_upstream=_require_upstream(
                    spec.get("ui_upstream"), f"app {app_id!r} ui_upstream"
                ),
                api_upstream=_require_upstream(
                    spec.get("api_upstream"), f"app {app_id!r} api_upstream"
                ),
                canonical_mount_path=_optional_mount_path(
                    spec.get("canonical_mount_path"),
                    f"app {app_id!r} canonical_mount_path",
                ),
            )

        artifact_paths = [mount.artifact_mount_path for mount in apps.values()]
        if len(set(artifact_paths)) != len(artifact_paths):
            raise JourneyConfigError("app canonical mount paths must be unique")

        journeys: dict[str, Journey] = {}
        for key, spec in raw_journeys.items():
            if not isinstance(key, str) or not _APP_ID.match(key):
                raise JourneyConfigError(f"journey key {key!r} must match {_APP_ID.pattern}")
            if not isinstance(spec, Mapping):
                raise JourneyConfigError(f"journey {key!r} spec must be a mapping")
            raw_ids = spec.get("apps")
            if not isinstance(raw_ids, (list, tuple)) or not raw_ids:
                raise JourneyConfigError(f"journey {key!r} needs a non-empty 'apps' list")
            app_ids: list[str] = []
            for app_id in raw_ids:
                if app_id not in apps:
                    # A journey may legitimately reference an app this installation does not
                    # deploy; it is then shown with the apps that ARE present. An id that is
                    # in no journey and in no deployment is still an error, caught above.
                    if only_apps is not None and app_id in raw.get("apps", {}):
                        continue
                    raise JourneyConfigError(f"journey {key!r} references unknown app {app_id!r}")
                if app_id in app_ids:
                    raise JourneyConfigError(f"journey {key!r} lists app {app_id!r} twice")
                app_ids.append(app_id)
            if not app_ids:
                # Every app in this journey belongs to another installation. Dropping the
                # journey is right: an empty journey in the nav is a dead end for the user.
                continue
            journeys[key] = Journey(
                key=key,
                label=_require_str(spec.get("label"), f"journey {key!r} label"),
                blurb=_require_str(spec.get("blurb"), f"journey {key!r} blurb"),
                app_ids=tuple(app_ids),
            )

        if not journeys:
            raise JourneyConfigError(
                "no journey has any deployed app; check the deployed-app list against the config"
            )

        return cls(apps=apps, journeys=journeys)

    def app(self, app_id: str) -> AppMount:
        """Resolve a mounted app, or raise :class:`UnknownApp` (a 404, never a leak)."""
        try:
            return self.apps[app_id]
        except KeyError as exc:
            raise UnknownApp(app_id) from exc

    def journey(self, key: str) -> Journey:
        try:
            return self.journeys[key]
        except KeyError as exc:
            raise UnknownJourney(key) from exc

    def list_journeys(self) -> tuple[Journey, ...]:
        return tuple(self.journeys.values())

    def apps_for(self, journey_key: str) -> tuple[AppMount, ...]:
        return tuple(self.apps[a] for a in self.journey(journey_key).app_ids)

    def validate_for_profile(self, profile: str) -> None:
        """Apply deployment constraints after the portable catalog is parsed."""
        if profile in {"gcp", "platform"}:
            for mount in self.apps.values():
                validate_secure_upstream(mount.ui_upstream)
                validate_secure_upstream(mount.api_upstream)


def api_target(mount: AppMount, tail: str) -> str:
    """The upstream backend URL for a proxied API call (the ``/apps/<id>/api`` prefix is stripped).

    ``tail`` is the path AFTER ``/apps/<id>/api/`` (FastAPI's ``{tail:path}`` capture), e.g.
    ``v1/cdd`` -> ``<api_upstream>/v1/cdd``. The backend is not basePath-aware; it serves at root.
    """
    return f"{mount.api_upstream}/{tail.lstrip('/')}"


def ui_target(mount: AppMount, full_path: str) -> str:
    """The upstream UI URL for a proxied document/asset call (full path forwarded unchanged).

    The app emits its configured canonical path, so the portal forwards that path verbatim.
    """
    return f"{mount.ui_upstream}{full_path}"
