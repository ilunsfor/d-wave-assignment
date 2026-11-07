"""Every tunable constant in one place.

Nothing else in the package hard-codes a number that changes behaviour. To retune the agent,
edit this file and re-run the three pinned PRs. Reasoning for each decision is in README.md.
"""

import os

# --- models ------------------------------------------------------------------------------
# Strong model: review, verify, synthesize. Cheap model: the one judgement call in triage
# (a two-sentence PR-intent summary). Both overridable so a model change is a deploy, not a diff.
REVIEW_MODEL = os.environ.get("REVIEW_MODEL", "gpt-4.1")
TRIAGE_MODEL = os.environ.get("TRIAGE_MODEL", "gpt-4.1-nano")

# --- price table, USD per 1M tokens --------------------------------------------------------
# Recorded 2026-11-07 from third-party trackers citing developers.openai.com/api/docs/pricing
# (Oct 2026). Confidence: moderate -- the official page was not fetched directly. Verify before
# quoting a cost figure externally. Unknown models cost 0 and are flagged in the metrics.
PRICES_RECORDED = "2026-11-07"
PRICES = {
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
}

# --- size routing --------------------------------------------------------------------------
# SMALL: whole diff in one call. MEDIUM: one call per file. LARGE: rank, review top N, list rest.
# A PR must satisfy BOTH limits of a tier to land in it; otherwise it falls to the next tier.
SMALL_MAX_FILES = 5
SMALL_MAX_CHANGED_LINES = 200
MEDIUM_MAX_FILES = 15
MEDIUM_MAX_CHANGED_LINES = 2000
LARGE_TOP_N = 12
# A single review unit above this many characters is skipped and listed -- never truncated.
UNIT_MAX_CHARS = 40_000

# --- ranking weights -----------------------------------------------------------------------
# Files are ordered by kind first (the KIND_WEIGHT order), then by the additive score below
# within a kind. Bonuses therefore reorder files of the same kind and can never lift a test or
# docs file above a source file.
KIND_WEIGHT = {"source": 50, "test": 20, "docs": 10, "config": 5}
HOT_PATH_PREFIXES = (
    "django/db/backends/",
    "django/db/models/sql/",
    "django/db/migrations/",
)
HOT_PATH_BONUS = 20
NEW_FILE_BONUS = 15
DELETED_FILE_PENALTY = -30
SIZE_DIVISOR = 20  # changed_lines / SIZE_DIVISOR ...
SIZE_BONUS_CAP = 20  # ... capped here, so size is a tiebreak and never outranks kind

# --- file classification -------------------------------------------------------------------
TEST_PATH_MARKERS = ("/tests/", "/testing/")  # anywhere in the path; a leading "tests/" also counts
TEST_BASENAMES = ("tests.py", "runtests.py")
TEST_BASENAME_PREFIXES = ("test_",)
TEST_BASENAME_SUFFIXES = ("_tests.py", "_test.py")
DOCS_PATH_PREFIXES = ("docs/",)
DOCS_EXTENSIONS = (".txt", ".rst", ".md")
CONFIG_BASENAMES = (
    "setup.py", "setup.cfg", "pyproject.toml", "tox.ini", "MANIFEST.in",
    ".pre-commit-config.yaml", ".editorconfig", ".gitattributes", ".gitignore", "package.json",
)
CONFIG_EXTENSIONS = (".ini", ".toml", ".cfg", ".yaml", ".yml", ".json")

# --- severity and verdicts -----------------------------------------------------------------
SEVERITIES = ("blocking", "should-fix", "nit")
EMIT_SEVERITIES = ("blocking", "should-fix")  # nit only with --nits
VERDICTS = ("supported", "unsupported", "uncertain")
# An `uncertain` finding is dropped unless its severity is in this set; then it is emitted with
# an explicit "unverified" marker rather than silently promoted or silently dropped.
EMIT_UNCERTAIN_SEVERITIES = ("blocking",)

# --- execution -----------------------------------------------------------------------------
MAX_WORKERS = 4  # bounded fan-out for review and verify; synthesis is always one call
CALL_TIMEOUT_S = 120  # per model call, so the latency budget is real
MAX_RETRIES = 1  # one retry on transient errors (timeout, connection, 429, 5xx). No more.
TEMPERATURE = 0
MAX_OUTPUT_TOKENS = 4000
# Characters of diff handed to the cheap model for the PR-intent summary.
TRIAGE_SUMMARY_MAX_CHARS = 12_000

# --- paths ---------------------------------------------------------------------------------
PINS_DIR = "pins"
RUNS_DIR = "runs"
GITHUB_PR_RE = r"^https?://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)/?(?:\.diff)?$"
