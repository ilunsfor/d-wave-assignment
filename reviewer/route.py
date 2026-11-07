"""Pick a tier from the diff's size and build the review units. Deterministic; thresholds in config.

SMALL  -> one unit: the whole diff.
MEDIUM -> one unit per file.
LARGE  -> rank, take the top N that fit the per-unit cap, list everything else as skipped.

Nothing is ever truncated. A file that will not fit a unit is skipped and named, with the reason.
"""

from dataclasses import dataclass, field

from . import config
from .schema import FileStat, Skipped, Unit
from .triage import rank, totals


@dataclass
class Plan:
    tier: str
    units: list[Unit]
    skipped: list[Skipped]
    ranked: list[FileStat]  # every file, in rank order, scores filled in
    totals: dict[str, int] = field(default_factory=dict)

    @property
    def reviewed_paths(self) -> list[str]:
        return [p for u in self.units for p in u.files]

    def counts(self) -> dict[str, int]:
        return {
            "files_total": len(self.ranked),
            "files_ranked": len(self.ranked),
            "files_reviewed": len(self.reviewed_paths),
            "files_skipped": len(self.skipped),
            "units": len(self.units),
        }


def tier_for(n_files: int, changed_lines: int) -> str:
    if n_files <= config.SMALL_MAX_FILES and changed_lines <= config.SMALL_MAX_CHANGED_LINES:
        return "SMALL"
    if n_files <= config.MEDIUM_MAX_FILES and changed_lines <= config.MEDIUM_MAX_CHANGED_LINES:
        return "MEDIUM"
    return "LARGE"


def route(files: list[FileStat]) -> Plan:
    ranked = rank(files)
    t = totals(files)
    tier = tier_for(t["files"], t["changed"])

    if tier == "SMALL":
        whole = "".join(f.hunk_text for f in files)  # diff order, exactly as served
        if len(whole) <= config.UNIT_MAX_CHARS:
            return Plan(tier, [Unit("whole-diff", [f.path for f in files], whole)], [], ranked, t)
        # Few lines but enormous bytes (minified assets, fixtures): one call would be a bad call.
        tier = "MEDIUM"

    if tier == "MEDIUM":
        units, skipped = _per_file_units(ranked, limit=None)
        return Plan(tier, units, skipped, ranked, t)

    units, skipped = _per_file_units(ranked, limit=config.LARGE_TOP_N)
    return Plan("LARGE", units, skipped, ranked, t)


def _per_file_units(ranked: list[FileStat], limit: int | None) -> tuple[list[Unit], list[Skipped]]:
    units: list[Unit] = []
    skipped: list[Skipped] = []
    for i, f in enumerate(ranked, start=1):
        if f.chars > config.UNIT_MAX_CHARS:
            skipped.append(Skipped(f.path, f"exceeds per-unit cap ({f.chars:,} chars > {config.UNIT_MAX_CHARS:,}); not truncated"))
            continue
        if limit is not None and len(units) >= limit:
            skipped.append(Skipped(f.path, f"outside top-{limit} budget (ranked {i} of {len(ranked)}, {f.kind}, score {f.score:g})"))
            continue
        units.append(Unit(f.path, [f.path], f.hunk_text))
    return units, skipped


def describe(plan: Plan) -> str:
    """Human-readable plan for --plan-only and for the review header."""
    t = plan.totals
    lines = [
        f"tier {plan.tier}: {t['files']} files, {t['changed']} changed lines "
        f"(SMALL <= {config.SMALL_MAX_FILES} files & {config.SMALL_MAX_CHANGED_LINES} lines; "
        f"MEDIUM <= {config.MEDIUM_MAX_FILES} & {config.MEDIUM_MAX_CHANGED_LINES}; LARGE top {config.LARGE_TOP_N})",
        f"units: {len(plan.units)}   reviewed files: {len(plan.reviewed_paths)}   skipped: {len(plan.skipped)}",
    ]
    for u in plan.units:
        lines.append(f"  review  {u.name}  ({len(u.text):,} chars, {len(u.files)} file{'s' if len(u.files) != 1 else ''})")
    for s in plan.skipped:
        lines.append(f"  skip    {s.path}  -- {s.reason}")
    return "\n".join(lines)
