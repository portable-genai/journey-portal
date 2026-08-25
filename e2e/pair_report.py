"""Read both targets' dossiers, compare them, and FAIL if they disagree.

This is the command practices check F4 rests on. It exits non-zero on any divergence in the
compared set, because a report that only prints is the failure mode being fixed: a guard nobody
can fail is indistinguishable from no guard, and a summary that counts outcomes reports a skip as
a pass.

    make e2e-local && make e2e-gcp && make e2e-pair

It deliberately does NOT run the journeys itself. Producing the two dossiers takes a browser, a
live deployment and, on the managed target, over a minute of real work; a command that silently
re-ran them would make "the pair agreed" mean "the pair agreed on whatever I just generated",
which is a different and weaker claim than "the two runs a reviewer watched agreed".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pairing import EXEMPT, PairingError, compare, load_dossier  # noqa: E402

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--left", default="local", help="the first target's out/ directory")
    parser.add_argument("--right", default="gcp", help="the second target's out/ directory")
    args = parser.parse_args()

    left_path = OUT / args.left / "dossier.json"
    right_path = OUT / args.right / "dossier.json"

    try:
        left = load_dossier(left_path)
        right = load_dossier(right_path)
        report = compare(left, right, args.left, args.right)
    except PairingError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2

    out_dir = OUT / "pair"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "comparison.json").write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )

    print(f"paired comparison: {args.left} vs {args.right}")
    print(f"  compared {len(report.compared)} deterministic fields")
    for name in report.compared:
        print(f"    {name}")
    print(f"  declared reductions, each with its reason: {len(EXEMPT)}")

    if report.agreed:
        print("\nPASS the two profiles agree on every consequential figure, check, escalation")
        print(f"     reason and citation relationship. Evidence: {out_dir / 'comparison.json'}")
        return 0

    print(
        f"\nFAIL {len(report.divergences)} field(s) diverge between the profiles:",
        file=sys.stderr,
    )
    for d in report.divergences:
        print(f"  {d.field}", file=sys.stderr)
        print(f"    {args.left}: {d.left!r}", file=sys.stderr)
        print(f"    {args.right}: {d.right!r}", file=sys.stderr)
    print(
        "\nThese are policy, not quality. Either the profiles genuinely disagree, which is the\n"
        "portability claim failing and is what this command is for, or the field belongs in\n"
        "pairing.EXEMPT with a written reason. Do not add it there to make this go green.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
