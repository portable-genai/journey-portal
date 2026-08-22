#!/usr/bin/env python3
"""Plan or apply a deterministic repository rename in a clean scratch clone."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--package", required=True)
    cli.add_argument("--project", required=True)
    cli.add_argument("--cli", required=True)
    cli.add_argument("--env-prefix", required=True)
    cli.add_argument("--resource-prefix", required=True)
    cli.add_argument("--apply", action="store_true", help="Modify this clean scratch clone")
    return cli


def main() -> int:
    args = parser().parse_args()
    if not args.package.replace("_", "").isalnum() or not args.package[0].isalpha():
        raise SystemExit("--package must be a Python identifier")
    # The distribution name and the console-script name are the same token here, so a bare
    # replacement cannot tell them apart and the second entry would silently win. The
    # console-script line is anchored on its whole declaration and rewritten FIRST, which is
    # what keeps --cli and --project independently meaningful.
    old_console_script = 'journey-portal = "journey_portal.cli.main:main"'
    new_console_script = f'{args.cli} = "{args.package}.cli.main:main"'
    replacements = {
        old_console_script: new_console_script,
        "journey_portal": args.package,
        "journey-portal": args.project,
        "PORTAL_": f"{args.env_prefix.upper()}_",
        'default     = "hrz9"': f'default     = "{args.resource_prefix}"',
    }
    edits: list[tuple[Path, str]] = []
    for path in tracked_files():
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        updated = text
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if updated != text:
            edits.append((path, updated))
    package_from = ROOT / "src" / "journey_portal"
    package_to = ROOT / "src" / args.package
    print(f"{'APPLY' if args.apply else 'DRY RUN'}: {len(edits)} text files")
    print(f"MOVE: {package_from.relative_to(ROOT)} -> {package_to.relative_to(ROOT)}")
    if args.apply:
        if subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True).stdout:
            raise SystemExit("--apply requires a clean scratch clone")
        for path, updated in edits:
            path.write_text(updated)
        package_from.rename(package_to)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
