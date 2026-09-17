"""``journey-portal`` CLI: inspect and validate the journey config, or serve the BFF.

Stdlib argparse only (no extra runtime dependency). ``validate`` is the fail-closed config check a
deploy can run before starting: it exits 2 on a malformed config, 0 when the catalog builds.
"""

from __future__ import annotations

import argparse
import sys

from ..config import Settings, build_container, load_journeys_mapping
from ..domain.catalog import JourneyCatalog
from ..domain.errors import JourneyConfigError


def _cmd_journeys(_: argparse.Namespace) -> int:
    catalog = build_container(Settings.load()).catalog
    for journey in catalog.list_journeys():
        print(f"{journey.key}: {journey.label} - {journey.blurb}")
        for mount in catalog.apps_for(journey.key):
            print(f"    {mount.app_id:6} {mount.label}")
            print(f"           ui  {mount.mount_path}/  ->  {mount.ui_upstream}")
            print(f"           api {mount.api_mount_path}/  ->  {mount.api_upstream}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    settings = Settings.load()
    path = args.path or settings.journeys_path
    try:
        catalog = JourneyCatalog.from_mapping(load_journeys_mapping(path))
    except (JourneyConfigError, FileNotFoundError, TypeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {len(catalog.apps)} apps, {len(catalog.journeys)} journeys in {path}")
    return 0


def _cmd_serve(_: argparse.Namespace) -> int:
    from ..api.app import main as serve_main

    serve_main()
    return 0


def main() -> None:
    # Configured first, and inside `main` rather than at module scope, because this CLI's
    # installed entry point IS this function (`[project.scripts]` names `cli.main:main`), so
    # this line runs in the shipped process. Idempotent in the kit, so a process that is both
    # this CLI and the API app configures once rather than logging every line twice. A rejected
    # profile stays the command's error to report: `validate` turns it into a clean exit 2, and
    # raising here would replace that sentence with a traceback.
    from hex_service_kit.logging import configure_logging

    from ..config import resolve_profile

    try:
        _profile = resolve_profile().profile
    except Exception:  # noqa: BLE001 - never pre-empt `validate`'s own clean exit
        _profile = "local"
    configure_logging(_profile, service="journey-portal")

    parser = argparse.ArgumentParser(prog="journey-portal", description="Journey Portal Shell CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_journeys = sub.add_parser("journeys", help="List the configured journeys and their apps.")
    p_journeys.set_defaults(func=_cmd_journeys)

    p_validate = sub.add_parser("validate", help="Validate the journeys config (exit 2 on error).")
    p_validate.add_argument(
        "--path", default=None, help="Path to a journeys YAML (default: config)."
    )
    p_validate.set_defaults(func=_cmd_validate)

    p_serve = sub.add_parser("serve", help="Run the portal BFF (uvicorn).")
    p_serve.set_defaults(func=_cmd_serve)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
