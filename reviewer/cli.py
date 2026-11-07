"""Command line entry point and pipeline orchestration.

    python -m reviewer <pr-url | path> [--plan-only] [--nits] [--out runs]

Phases, in order: fetch -> triage -> route -> review -> verify -> groundedness -> synth -> report.
Each phase is timed into the ledger; the ledger becomes the metrics sidecar next to the review.
"""

import argparse
import sys
from pathlib import Path

from . import config, review as review_mod, route, synth, triage, verify as verify_mod
from .fetch import FetchError, fetch
from .llm import Client, LLMError, load_dotenv
from .metrics import Ledger


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="reviewer", description="Size-adaptive code review for django/django PRs.")
    p.add_argument("source", nargs="?", help="GitHub PR URL or path to a unified diff")
    p.add_argument("--diff", dest="diff_path", help="path to a unified diff (alternative to the positional source)")
    p.add_argument("--plan-only", action="store_true", help="triage and route only; no model calls, no key needed")
    p.add_argument("--nits", action="store_true", help="also emit nit-severity findings")
    p.add_argument("--out", default=config.RUNS_DIR, help=f"output directory (default: {config.RUNS_DIR}/)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.diff_path or args.source
    if not source:
        build_parser().print_usage()
        return 2

    ledger = Ledger(source=source)
    try:
        with ledger.phase("fetch"):
            text, label = fetch(source)
        with ledger.phase("triage"):
            files = triage.parse_diff(text)
    except (FetchError, triage.ParseError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    ledger.label = label

    with ledger.phase("route"):
        plan = route.route(files)
    print(f"{label}: {route.describe(plan)}")

    if args.plan_only:
        print("\nrank  score  kind    flags  +add  -del  path")
        for i, f in enumerate(plan.ranked, 1):
            flags = "".join(c for c, on in (("N", f.is_new), ("D", f.is_deleted), ("R", f.is_rename)) if on) or "-"
            print(f"{i:4}  {f.score:5.1f}  {f.kind:6}  {flags:5}  {f.added:<4}  {f.removed:<4}  {f.path}")
        return 0

    load_dotenv()
    try:
        client = Client(ledger)
    except LLMError as e:
        print(f"error: {e}. Use --plan-only to inspect routing without a key.", file=sys.stderr)
        return 1

    review_md = run_review(label, files, plan, client, ledger, emit_nits=args.nits)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{label}.md").write_text(review_md, encoding="utf-8")
    ledger.write(out / f"{label}.metrics.json")
    r = ledger.report()
    print(f"\nwrote {out / f'{label}.md'} and {out / f'{label}.metrics.json'}")
    print(f"{r['latency_seconds']['total']:.1f}s, ${r['cost']['usd_total']:.4f}, "
          f"emitted {ledger.counts.get('findings_emitted', 0)} finding(s), "
          f"groundedness {ledger.counts.get('groundedness', {}).get('grounded', 0)}"
          f"/{ledger.counts.get('groundedness', {}).get('total', 0)}")
    return 0


def run_review(label: str, files: list, plan: route.Plan, client: Client, ledger: Ledger, emit_nits: bool) -> str:
    """The pipeline below the routing fork. Returns the review markdown; fills the ledger."""
    by_path = {f.path: f for f in files}
    ledger.counts.update(plan.counts(), tier=plan.tier)

    with ledger.phase("triage"):
        try:
            intent = triage.intent_summary(files, client)
        except LLMError as e:
            intent = ""
            ledger.note(f"triage: intent summary failed ({e}); reviewers ran without it")

    with ledger.phase("review"):
        drafted = review_mod.review(plan.units, intent, client)
    kept, suppressed = review_mod.suppress(drafted.findings, emit_nits)

    with ledger.phase("verify"):
        verified = verify_mod.verify(kept, by_path, client)
    survivors, rejected = verify_mod.apply_verdicts(verified.findings)

    with ledger.phase("groundedness"):
        emitted, ungrounded = verify_mod.ground(survivors, by_path)

    drafted_n = len(drafted.findings) + drafted.malformed
    ledger.counts.update(
        findings_drafted=drafted_n,
        findings_malformed=drafted.malformed,
        findings_suppressed=suppressed,
        findings_verified=len(kept),
        findings_rejected=rejected,
        findings_ungrounded=ungrounded,
        findings_emitted=len(emitted),
        findings_unverified_emitted=sum(1 for f in emitted if f.verdict != "supported"),
        groundedness={
            "grounded": len(survivors) - ungrounded,
            "total": len(survivors),
            "rate": round((len(survivors) - ungrounded) / len(survivors), 3) if survivors else None,
        },
        verifier_rejection_rate=round(rejected / len(kept), 3) if kept else None,
        verifier_failed_files=verified.failed_files,
        review_failed_units=len({s.path for s in drafted.failed}),
        findings=[{**f.public(), "verdict": f.verdict, "verdict_reason": f.verdict_reason} for f in emitted],
    )
    if plan.units and len(drafted.failed) == len(plan.reviewed_paths):
        ledger.note("review: every unit failed; the review below contains no findings because nothing was reviewed")

    not_reviewed = plan.skipped + drafted.failed
    with ledger.phase("synth"):
        return synth.synthesize(label, plan, intent, emitted, not_reviewed, client, ledger)
