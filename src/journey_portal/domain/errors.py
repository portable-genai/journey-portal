"""Domain error hierarchy (pure stdlib)."""

from __future__ import annotations


class PortalError(Exception):
    """Base class for every portal domain error."""


class JourneyConfigError(PortalError):
    """The journeys config is malformed or internally inconsistent (fail fast at load)."""


class UnknownApp(PortalError):
    """A request referenced an app id that is not mounted in the catalog (maps to HTTP 404)."""


class UnknownJourney(PortalError):
    """A request referenced a journey key that is not defined (maps to HTTP 404)."""
