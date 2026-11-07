"""Command line entry point: `python -m reviewer <pr-url | path> [--plan-only] [--nits]`."""

import argparse
import sys

from . import route, triage
from .fetch import FetchError, fetch


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reviewer", description="Size-adaptive code review for django/django PRs.")
    p.add_argument("source", nargs="?", help="GitHub PR URL or path to a unified diff")
    p.add_argument("--diff", dest="diff_path", help="path to a unified diff (alternative to the positional source)")
    p.add_argument("--plan-only", action="store_true", help="triage and route only; no model calls, no key needed")
    p.add_argument("--nits", action="store_true", help="also emit nit-severity findings")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.diff_path or args.source
    if not source:
        build_parser().print_usage()
        return 2
    try:
        text, label = fetch(source)
        files = triage.parse_diff(text)
    except (FetchError, triage.ParseError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    plan = route.route(files)
    print(f"{label}: {route.describe(plan)}")
    if args.plan_only:
        print("\nrank  score  kind    flags  +add  -del  path")
        for i, f in enumerate(plan.ranked, 1):
            flags = "".join(c for c, on in (("N", f.is_new), ("D", f.is_deleted), ("R", f.is_rename)) if on) or "-"
            print(f"{i:4}  {f.score:5.1f}  {f.kind:6}  {flags:5}  {f.added:<4}  {f.removed:<4}  {f.path}")
        return 0

    print("error: the review pipeline is not wired yet; use --plan-only", file=sys.stderr)
    return 1
