"""Command line entry point: `python -m reviewer <pr-url | path> [--plan-only]`."""

import argparse
import sys

from . import triage
from .fetch import FetchError, fetch


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reviewer", description="Size-adaptive code review for django/django PRs.")
    p.add_argument("source", nargs="?", help="GitHub PR URL or path to a unified diff")
    p.add_argument("--diff", dest="diff_path", help="path to a unified diff (alternative to the positional source)")
    p.add_argument("--plan-only", action="store_true", help="triage and route only; no model calls")
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

    t = triage.totals(files)
    print(f"{label}: {t['files']} files, +{t['added']} -{t['removed']} "
          f"({t['source_files']} source, {t['test_files']} test, {t['docs_files']} docs, {t['config_files']} config)")
    for f in files:
        flags = "".join(c for c, on in (("N", f.is_new), ("D", f.is_deleted), ("R", f.is_rename)) if on) or "-"
        print(f"  {f.kind:6} {flags:2} +{f.added:<4} -{f.removed:<4} {f.path}")
    return 0
