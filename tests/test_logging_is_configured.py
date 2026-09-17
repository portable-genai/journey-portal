"""The service configures the fleet's logging formatter, in the process that actually ships.

This exists because the deployed service did not. Read live against the reference deployment
on 2026-09-15: of roughly 60 `severity>=ERROR` entries on `cloud_run_revision` in thirty days,
every one came from the platform's own request log or from raw stderr. Python tracebacks
arrived as multi-line `textPayload` fragments split across several entries, with no `severity`
from the application, no logger name and no trace field. `configure_logging` was called in 75
files across the workspace and in none of the seven deployed stacks.

What that cost is specific rather than cosmetic. Cloud Logging reads `severity` from the
payload, so an application error was indistinguishable from an info line and could not drive a
log-based metric. `logging.googleapis.com/trace` is what puts a log line inside the request it
came from. And a traceback split across N entries has no single entry carrying the exception,
which is what Error Reporting groups on.

The assertions are about the SHIPPED shape rather than about the kit, which has its own tests.
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
from typing import Any

import pytest
from hex_service_kit.logging import reset_logging_for_tests

_PROFILE_ENV = "PORTAL_PROFILE"
_SERVED_MODULE = "journey_portal.api.app"
_SERVICE = "journey-portal"


@pytest.fixture(autouse=True)
def _clean_logging() -> Any:
    """Each test owns the root logger, and hands it back."""
    reset_logging_for_tests()
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    reset_logging_for_tests()
    root.handlers[:] = handlers
    root.setLevel(level)


def _reimport(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """Import the API module the way a shipped process does: at module scope."""
    monkeypatch.setenv(_PROFILE_ENV, profile)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    sys.modules.pop(_SERVED_MODULE, None)
    importlib.import_module(_SERVED_MODULE)


def test_importing_the_served_module_configures_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Dockerfile CMD serves the app OBJECT, so import must be enough.

    Proved red before it was trusted: with the module-scope `configure_logging` call removed,
    the root logger carries whatever the test runner left on it and this fails.
    """
    _reimport(monkeypatch, "gcp")
    root = logging.getLogger()
    assert len(root.handlers) == 1, "the kit installs exactly one handler"
    assert type(root.handlers[0].formatter).__name__ == "CloudLoggingFormatter"


def test_a_cloud_profile_error_is_one_json_object_the_platform_can_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The field names are load-bearing, so they are named rather than counted."""
    _reimport(monkeypatch, "gcp")
    formatter = logging.getLogger().handlers[0].formatter
    assert formatter is not None

    try:
        raise ValueError("downstream dependency refused the write")
    except ValueError:
        record = logging.LogRecord(
            name=f"{_SERVICE}.adapter",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    payload = json.loads(formatter.format(record))
    assert payload["severity"] == "ERROR"
    assert payload["service"] == _SERVICE
    # One entry carries the whole traceback, which is what Error Reporting groups on. A
    # traceback split across entries, which is what the deployment emitted, groups as nothing.
    assert "request failed" in payload["message"]
    assert "Traceback (most recent call last)" in payload["message"]
    assert "ValueError: downstream dependency refused the write" in payload["message"]


def test_the_offline_profile_stays_readable_at_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`local` is a person at a terminal running a demo, so it is text and not JSON."""
    _reimport(monkeypatch, "local")
    formatter = logging.getLogger().handlers[0].formatter
    assert type(formatter).__name__ == "Formatter"


def test_the_cli_entry_point_configures_logging_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`[project.scripts]` names `cli.main:main`, so the call belongs inside that function.

    This CLI is argparse rather than Typer, so there is no callback to register: the entry
    point IS `main`, and a call at module scope would run on import in every test too. The
    assertion is that invoking the entry point configures logging, which is what ships.

    `--help` is the argument on purpose. Logging is configured at the top of `main`, before
    the parser is built, so the cheapest invocation that reaches that line proves it. A real
    subcommand would build the container and fail on deployment configuration this test has
    no business supplying, which would assert something else entirely.
    """
    cli_main = importlib.import_module("journey_portal.cli.main")

    monkeypatch.setenv(_PROFILE_ENV, "gcp")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-project")
    monkeypatch.setattr(sys, "argv", ["journey-portal", "--help"])
    try:
        cli_main.main()
    except SystemExit:
        pass
    assert type(logging.getLogger().handlers[0].formatter).__name__ == "CloudLoggingFormatter"


def test_the_cli_never_pre_empts_a_profile_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected profile stays `validate`'s error to report, not the logging call's."""
    cli_main = importlib.import_module("journey_portal.cli.main")

    monkeypatch.setenv(_PROFILE_ENV, "NotAProfile")
    monkeypatch.setattr(sys, "argv", ["journey-portal", "--help"])
    try:
        cli_main.main()
    except SystemExit:
        pass
    assert logging.getLogger().handlers, "logging is still configured on a bad profile"
