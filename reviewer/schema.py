"""Data types shared across the pipeline, plus the enforced finding schema.

The model-facing JSON schemas live here too, so the contract the model is held to and the
validation that enforces it cannot drift apart.
"""

from dataclasses import asdict, dataclass
from typing import Any

from . import config

KINDS = ("source", "test", "docs", "config")
TIERS = ("SMALL", "MEDIUM", "LARGE")


class SchemaError(ValueError):
    """A model response did not match the contract it was given."""


@dataclass
class FileStat:
    """Per-file facts derived deterministically from the diff."""

    path: str
    kind: str
    added: int
    removed: int
    is_new: bool
    is_deleted: bool
    is_rename: bool
    hunk_text: str  # this file's hunks exactly as served; used for review and groundedness
    score: float = 0.0

    @property
    def changed(self) -> int:
        return self.added + self.removed

    @property
    def chars(self) -> int:
        return len(self.hunk_text)


@dataclass
class Unit:
    """One review call: a name, the files it covers, and the text the model sees."""

    name: str
    files: list[str]
    text: str


@dataclass
class Skipped:
    path: str
    reason: str


@dataclass
class Finding:
    # --- the enforced schema (what the model must produce) ---
    file: str
    line_start: int
    line_end: int
    snippet: str
    severity: str
    category: str
    finding: str
    suggestion: str
    # --- pipeline annotations, never shown to the model ---
    unit: str = ""
    verdict: str = ""  # supported | unsupported | uncertain | "" before verification
    verdict_reason: str = ""
    grounded: bool | None = None

    def public(self) -> dict[str, Any]:
        """The eight schema fields only."""
        d = asdict(self)
        for k in ("unit", "verdict", "verdict_reason", "grounded"):
            d.pop(k)
        return d


# --- model-facing JSON schemas --------------------------------------------------------------

_FINDING_PROPERTIES = {
    "file": {
        "type": "string",
        "description": "Path exactly as it appears in the diff header.",
    },
    "line_start": {
        "type": "integer",
        "description": "First line of the cited code, new-file numbering (old-file for deletions).",
    },
    "line_end": {"type": "integer"},
    "snippet": {
        "type": "string",
        "description": (
            "Verbatim code copied from the diff without the leading +/-/space prefix. "
            "One to five lines. Must appear character-for-character in the diff."
        ),
    },
    "severity": {"type": "string", "enum": list(config.SEVERITIES)},
    "category": {
        "type": "string",
        "description": "e.g. correctness, compatibility, tests, docs, security, performance, api-design",
    },
    "finding": {"type": "string", "description": "What is wrong, in one or two sentences."},
    "suggestion": {"type": "string", "description": "What to change. Concrete."},
}

REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "review_findings",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        **_FINDING_PROPERTIES,
                        # Internal: the reviewer's reasoning. Stripped before verification and
                        # never emitted, so the verifier judges the claim, not the argument.
                        "rationale": {"type": "string"},
                    },
                    "required": list(_FINDING_PROPERTIES) + ["rationale"],
                },
            }
        },
        "required": ["findings"],
    },
}

VERIFY_RESPONSE_SCHEMA: dict[str, Any] = {
    "name": "verify_findings",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "index": {"type": "integer"},
                        "verdict": {"type": "string", "enum": list(config.VERDICTS)},
                        "reason": {"type": "string"},
                    },
                    "required": ["index", "verdict", "reason"],
                },
            }
        },
        "required": ["verdicts"],
    },
}


# --- validation -----------------------------------------------------------------------------

def parse_findings(payload: Any, allowed_files: set[str], unit: str) -> tuple[list[Finding], int]:
    """Validate a reviewer response against the finding schema.

    Returns (valid findings, number of malformed findings dropped). Malformed findings are
    dropped, never repaired: a finding we had to guess at is not one we can stand behind.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
        raise SchemaError("response is not an object with a 'findings' list")
    valid: list[Finding] = []
    malformed = 0
    for raw in payload["findings"]:
        try:
            valid.append(_coerce_finding(raw, allowed_files, unit))
        except SchemaError:
            malformed += 1
    return valid, malformed


def _coerce_finding(raw: Any, allowed_files: set[str], unit: str) -> Finding:
    if not isinstance(raw, dict):
        raise SchemaError("finding is not an object")
    try:
        f = Finding(
            file=str(raw["file"]).strip(),
            line_start=int(raw["line_start"]),
            line_end=int(raw["line_end"]),
            snippet=str(raw["snippet"]),
            severity=str(raw["severity"]).strip().lower(),
            category=str(raw["category"]).strip(),
            finding=str(raw["finding"]).strip(),
            suggestion=str(raw["suggestion"]).strip(),
            unit=unit,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise SchemaError(f"missing or mistyped field: {e}") from e
    if f.file not in allowed_files:
        raise SchemaError(f"file {f.file!r} is not in the diff")
    if f.severity not in config.SEVERITIES:
        raise SchemaError(f"severity {f.severity!r} not in {config.SEVERITIES}")
    if f.line_start < 1 or f.line_end < f.line_start:
        raise SchemaError("bad line range")
    if not f.snippet.strip():
        raise SchemaError("empty snippet")
    if not f.finding:
        raise SchemaError("empty finding")
    return f


def parse_verdicts(payload: Any, count: int) -> dict[int, tuple[str, str]]:
    """Validate a verifier response. Returns {finding index: (verdict, reason)}."""
    if not isinstance(payload, dict) or not isinstance(payload.get("verdicts"), list):
        raise SchemaError("response is not an object with a 'verdicts' list")
    out: dict[int, tuple[str, str]] = {}
    for raw in payload["verdicts"]:
        try:
            i = int(raw["index"])
            v = str(raw["verdict"]).strip().lower()
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= i < count and v in config.VERDICTS:
            out[i] = (v, str(raw.get("reason", "")).strip())
    return out
