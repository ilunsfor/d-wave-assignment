# Size-adaptive code review for django/django

An agent that reviews a Django pull request and produces feedback a maintainer could post:
grounded in the diff, scoped to what it actually looked at, and willing to say nothing.

The three pinned PRs are a one-line wording fix, a 70-line bug fix, and a 43-file feature. **They
are three sizes, not three PRs**, and the central design decision is that the agent triages the
diff first and routes by size. Small diffs are reviewed whole in one call. Large diffs have their
files ranked and the top ones reviewed within a stated budget, with every skipped file listed and
the reason given. One pipeline over all three would fail at least one of them: a heavyweight pass
over a one-line change finds something because it was asked to, and a single call over 43 files
produces findings that cluster in the first files, cannot be partially recovered when it times
out, and are hard to verify.

## Running it

Python 3.11+, two dependencies.

```bash
pip install -r requirements.txt
```

Put `ANTHROPIC_API_KEY=...` in the environment or in the gitignored `.env` at the repo root
(the loader resolves it relative to the package, not the working directory), then:

```bash
python -m reviewer https://github.com/django/django/pull/19798
```

```bash
python -m reviewer --diff pins/pr-18056.diff
```

```bash
python -m reviewer --plan-only pins/pr-18056.diff
```

A PR URL is fetched as `<url>.diff` (no auth for public repos) and cached under `pins/`; the
three pinned diffs are committed and byte-identical to what GitHub served, so the runs in `runs/`
are reproducible offline. `--plan-only` prints the tier, the units, the ranking and every skip
with no model calls and no key. `--nits` also emits nit-severity findings. Output goes to
`runs/<label>.md` and `runs/<label>.metrics.json`.

```bash
python -m unittest discover -s tests
```

## What it produces, and why that form

A Markdown document: a short assessment, findings grouped by severity with `file:line` and the
verbatim snippet they are about, a **Not reviewed** section whenever anything was skipped, and a
metrics footer. A summary with file and line references is what a maintainer can act on without
tooling, and severity ranking is what lets "no blocking findings" be a one-line result instead of
a page of hedged observations. Inline PR comments were cut (see below); the document carries the
same information and can be posted as one comment.

## How it works

```
fetch -> triage -> route -+-> SMALL   whole diff, one call      -+
                          +-> MEDIUM  one call per file, fan-out -+-> suppress -> verify -> ground -> synth -> report
                          +-> LARGE   rank, top N, list the rest -+
```

| Phase | What | Model |
|---|---|---|
| fetch | PR URL or local diff; pins cached as raw bytes | none |
| triage | parse with `unidiff`, per-file stats, classify source/test/docs/config, score | none, except one claude-haiku-5-5 call for a two-sentence PR-intent summary |
| route | tier from file count and changed lines; build units; record skips with reasons | none |
| review | structured findings per unit, bounded fan-out, main thread is the only writer | claude-opus-5-5, effort high |
| suppress | drop nits before anything pays for them | none |
| verify | per file: findings' claims plus the file's hunks, **not** the reviewer's rationale; supported / unsupported / uncertain | claude-opus-5-5, effort high |
| ground | exact substring check of each snippet against the cited file's before- or after-image | none |
| synth | one call composes the review; header, Not-reviewed and footer rendered by code | claude-opus-5-5, effort medium |

Every constant lives in `reviewer/config.py`. Diff content enters every prompt through one
function that fences and labels it as untrusted data, because a diff is attacker-controlled text.

The models always think before answering and reject sampling parameters, so determinism is not
available and the per-phase lever is reasoning effort rather than temperature. Structured output
is enforced by the API against the same JSON schema the parser validates. A refusal or a
truncated response is a visible call failure, not a silent fallback to another model: the metrics
attribute cost per model, and a quiet substitution would mis-price the run.

### The decisions, and why

**Thresholds.** SMALL is at most 5 files and 200 changed lines; MEDIUM at most 15 and 2,000;
LARGE reviews the top 12 files. A file over 40,000 characters is skipped and listed, never
truncated. The consequence, stated plainly: #19798 (4 files, 73 lines) lands in SMALL with
#20426. A 70-line bug fix wants its source and its tests in one context, and fanning out four
tiny calls would lose exactly the cross-file view that catches a missing test case. The thresholds
were not bent so that each pinned PR hits a different tier; the MEDIUM path is covered by tests
and visible with `--plan-only` on any 6-to-15-file diff.

**Ranking.** Kind first: source over test over docs over config. Within a kind, an additive score:
hot DB paths (`django/db/backends/`, `django/db/models/sql/`, `django/db/migrations/`) +20, new
file +15, deleted file -30, and size as a capped tiebreak. Evidence that kind must dominate: the
twelve largest files in #18056 are eleven test files and one docs file. A size-first ranking
would review tests before `composite.py`.

**Blocking** is a defect visible in the diff that causes incorrect behaviour, a crash, data
corruption, a security issue, or a backward-incompatible change. **Should-fix** is what a
maintainer would require before merge without it being a correctness bug: a code path with no
test, a user-visible change with no release note, docs that disagree with code. **Nit** is style.

**Severity bar.** Blocking and should-fix are emitted; nits only with `--nits`, and they are
dropped before the verifier so they cost nothing. Verifier `unsupported` findings are dropped.
`uncertain` findings are dropped unless blocking, in which case they are emitted marked
*unverified* rather than silently promoted or silently lost.

## Metrics

Each run writes a JSON sidecar with wall-clock latency total and per phase; tokens in and out per
phase and per model; USD cost from a dated price table in `config.py` (rates recorded 2026-11-07
directly from the Claude API pricing page);
files total / ranked / reviewed / skipped; the finding funnel drafted / malformed / suppressed /
verifier-rejected / ungrounded / emitted; groundedness; and the verifier rejection rate.

**The quality signal is citation groundedness.** Every finding carries a file, a line range and a
verbatim snippet, and a deterministic check asserts the snippet appears character-for-character
in the before-image or the after-image of that file's hunks (context plus removed lines, or
context plus added lines). No whitespace normalisation, no fuzzy matching. It measures the failure that
actually gets review bots muted (citing code that is not there), it costs nothing per run so it
can gate CI on every prompt and model change, and it is aimed at exactly what a model does under
pressure to produce findings. The verifier rejection rate is the secondary signal: judged rather
than deterministic, so it is reported, not gated on.

Groundedness is the gate, not the headline. Across the three runs it was checked on four findings
(1/1, 2/2, 1/1), which has no statistical power; four passes show the check runs, not that the
model is reliable. The more informative quality number from these runs is the funnel itself: on
#18056 three findings were drafted, the severity bar suppressed one nit, the verifier rejected one
of the two it saw (a 50% rejection rate on what reached it), and one was emitted. That is the
pipeline doing work, and it is where a larger sample would first show a change.

### Predicted versus actual

Predictions were written before any live run (timestamped in the build log).

| | #20426 | #19798 | #18056 |
|---|---|---|---|
| Tier | SMALL | SMALL | LARGE, 12 of 43 files |
| Predicted wall clock (original; re-based: add 50% for thinking) | 10–25 s | 15–40 s | 60–150 s |
| Predicted cost (gpt-4.1 pricing, original) | < $0.01 | $0.01–0.03 | $0.15–0.40 |
| Predicted cost (re-based to claude-opus-5-5 after the provider swap, still before any run) | < $0.03 | $0.03–0.10 | $0.40–1.20 |
| Predicted emitted findings | 0 | 0–2 | 3–8 |
| Predicted groundedness | 100% or n/a | 100% | 80–95% |
| **Actual wall clock** | 20 s | 35 s | 36 s |
| **Actual cost** | $0.050 | $0.110 | $0.357 |
| **Actual emitted findings** | 1 | 2 | 1 |
| **Actual groundedness** | 1/1 | 2/2 | 1/1 |
| Actual drafted / verifier-rejected | 1 / 0 | 3 / 1 | 3 / 1 (plus 1 nit suppressed) |
| Actual files skipped | 0 | 0 | 31 of 43, all listed |

What the actuals say, run on 2026-11-07 (`runs/pr-*.md`, sidecars alongside):

- **Cost and latency** came in under the re-based predictions. The large PR's twelve review
  calls ran four at a time and the review phase took 25 s; the whole run was faster than the
  70-line PR's single call plus synthesis would suggest, because fan-out is wall-clock cheap.
- **#20426 emitted one finding where I predicted zero.** The reviewer argues the new wording
  ("the `error` argument is a dictionary") misdescribes the branch, which also fires for a
  `ValidationError` built from a dict, and the added test uses exactly that. The verifier
  supported it; it is grounded; the PR merged with that wording. A maintainer might post it or
  might not. It is the kind of should-fix the severity bar lets through, and I would rather show
  it than tune the bar after seeing the answer.
- **Every verifier rejection was for the same reason**: the claim depends on code outside the
  diff (whether a path is tested elsewhere, what a method not in the hunk does). That is the
  predicted failure mode, the policy handled it as designed, and it is why cross-file context
  is the first item under two more days.
- **Groundedness was 100% on every final run**, after one real bug in the check itself (below).
  Four findings is too few to call that a result; see the note under Metrics.
- **The MEDIUM tier never fired.** #20426 (2 files) and #19798 (4 files) both routed SMALL and
  #18056 went LARGE, so the per-file fan-out band is covered by unit tests and `--plan-only` but
  by no live run. That is a gap in the evidence. The tier exists because between 6 and 15 files
  a whole-diff call starts to lose per-file attention while per-file calls are still cheap; the
  thresholds were not moved to make a pinned PR land there, because that is fitting to the test
  set.
- **Run-to-run variance is large.** A first run of #18056, identical except for the triage
  summariser's input (see the AI-use log), drafted 7 findings and emitted 4; the committed run
  drafted 3 and emitted 1. Both are kept: `runs/pr-18056.run1.md`. The models always think and
  accept no sampling controls, so a single run is a sample, not the answer. For the platform
  this means the golden set must be run several times per change and judged on distributions;
  for a reviewer it means the deterministic numbers (groundedness, skips) are the ones to trust
  run to run, and the finding count is not.

### Against the human reviews

The brief chose closed PRs with real review history so the output could be compared. I did that
by hand after the runs, reading the review comments on both PRs through the GitHub API. The
agent's findings were not shown to it and it had no access to the review threads.

- **#18056, `django/db/models/aggregates.py:189-190`.** The agent's only emitted finding is on
  the `Count` rewrite for composite primary keys. Simon Charette commented on the same two lines
  in the real review, about a different thing: the rewrite counts one column and is only correct
  because every column of a composite primary key is `NOT NULL`. The agent argued instead that
  returning a fresh unresolved `Count` drops the resolved clone's state and any extra kwargs. The
  merged code kept `return Count(cols[0], filter=result.filter)`, so the maintainers either did
  not consider the agent's point or did not share it, and nobody has yet checked whether it is
  right. The overlap here is in location, not in substance.
- **#19798, `tests/foreign_object/tests.py:797-800`.** The agent said both exclude tests use a
  value that satisfies the constraint, so they pass whether or not exclusion works, and suggested
  skipping a `ForeignObject` whose `from_fields` intersect `exclude`. In the real review Jacob
  Walls asked for a test showing exclusion "still avoids validating a failing constraint" and
  Charette asked that excluding a composite member skip validation of the virtual field. The PR
  merged with the satisfying value and a follow-up ticket for the exclusion case. This overlap is
  in substance.
- **#19798, `django/db/models/base.py:1375-1377`.** The agent said `getattr(self, from_field)` can
  return a related instance rather than a column value and suggested `field.local_related_fields`
  and their `attname`s. Walls had flagged the same code on an earlier revision as a performance
  regression and pointed at `field.local_related_fields` as the fix. Charette's comment about
  `GenericForeignKey` and `ManyToManyField` returning managers, which looks similar, was about a
  different branch of the same function. The overlap is in the suggested fix more than in the
  stated problem.

The agent did not find these because it understood composite primary keys or foreign objects. It
found them because the ranking put those files in front of a strong model with a narrow brief and
a verifier behind it. Three overlaps with human reviewers on three PRs is encouraging and is not
a measurement; the golden set in DESIGN.md is how it would become one.

The honest claim this submission makes is narrow: the agent produces grounded, scoped review
output at a known cost and latency, and it tells you what it did not look at. It does not claim
to find bugs humans missed.

## What was built versus cut

Built: the pipeline above, the three pinned diffs, unit tests for parsing, classification,
ranking, routing, groundedness and the verdict policy, and an end-to-end test over #20426 with a
fake model that checks the funnel counts, that the verifier never sees the reviewer's rationale,
and that a finding the synthesizer drops is appended rather than lost.

Cut, deliberately:

- **A django clone and symbol index.** Review is from the diff plus the context the hunks carry.
  Cross-file understanding is a genuinely better product and a genuinely wrong use of four hours.
  It is the first thing two more days would buy, and the reason #18056 is reviewed shallowly.
- **GitHub write access and inline posting.** The deliverable is review text a human posts. An
  agent that can post is an agent that needs a credential, an identity and a false-positive rate
  low enough to be trusted with one.
- **An MCP server.** Architecture only, in DESIGN.md.
- **Multi-provider support.** One model API, overridable by environment variable.
- **Caching beyond the pinned diffs**, and a config system beyond one file of constants.
- **Retries beyond one.** A single retry on transient errors, then a visible failure.

## How AI was used

Tooling: Claude (chat) drafted a build prompt from the assignment; Claude Code executed it
stage by stage with a commit per stage. Workflow: the prompt made the four product decisions
(thresholds, ranking weights, what blocks, the severity bar) explicit items to be proposed with
reasoning and accepted by me, rather than defaults the model invented. A running log of what the
AI got wrong was kept as it happened; the entries below are from that log, not reconstructed.

- The chat-drafted prompt said #18056 "blows the context window". Measured, it is about 35k
  tokens and fits a 1M-token window several times over. Caught by fetching and counting the diffs
  before writing code; the routing thesis was re-grounded on review quality, recoverability,
  verifiability and cost instead.
- The chat-drafted prompt said #19798 includes a release note. The diff has none. Caught by
  listing the diff's files.
- Claude Code assumed the 1.x `openai` SDK; the installed version is 3.x. Caught by introspecting
  the package before writing the client.
- Its first size table came from a grep that undercounted #18056's added lines by about 400
  (lines starting `++` in docs). `unidiff`'s counts replaced it.
- It wrote a synthetic test fixture whose hunk header did not match its body; three parser tests
  errored on the fixture. Caught on the first test run; the real pins parsed cleanly.
- It committed the ranking stage with a failing test because it piped the test run through
  `tail`, masking the exit code. The failing test was real: the purely additive weights let a
  412-line new test file (20 + 15 + 20) outrank 50-point source edits in #18056. Fixed in the
  next commit by making kind lexicographically dominant; test runs now use `pipefail`.
- It wrote an end-to-end test whose verdict list assumed findings kept their submission order;
  the review pass sorts them for determinism. Test bug, not code bug, but it took one failed run
  to see.
- The chat-drafted prompt chose OpenAI models, and Claude Code carried that choice through
  seven stages without questioning it. The assignment says only "use your own LLM API key". When
  I asked, the provider was swapped to the Claude API before any live run: one module, the price
  table, and this document changed; nothing in review, verify, synth or the tests did.
- The first live run dropped a correct finding as ungrounded. The groundedness check matched
  snippets against the hunk text with context, removed and added lines interleaved; the model
  quoted two lines of the *new* code, which are adjacent in the file but have the removed line
  between them in the hunk. Design error, not a model error: "the diff as text" is not "the
  code". Fixed to check the before- and after-images; three tests added for the case. The
  sidecar now lists every dropped finding with the stage that dropped it, which is how this was
  diagnosed in one run instead of guessing.
- On the same run the triage summary claimed two string fragments were concatenated without a
  space. I first logged that as the cheap model misreading the diff. Wrong: my code stripped the
  `+`/`-` markers before handing it the hunks, so the removed line and its replacement arrived
  as two consecutive lines of code. The summariser read exactly what it was given. Fixed by
  passing the hunks as a diff, with a test. The reviewer, which sees the real diff, had not
  taken the bait either time.
- A second chat-drafted prompt, for the cleanup pass, opened with a git-hygiene section built on
  the claim that the worktree copy and the assignment docx were tracked. Neither was; `git
  ls-files` showed it in one command. The section shrank to two ignore lines. The same prompt's
  comparison against the human reviews was checked comment by comment against the GitHub API
  before it was written up: two of its three overlaps held as stated and the third was weaker
  than claimed, which is how the section above is worded.
- The committed runs predate a cosmetic change to how skipped files are labelled (position
  within kind rather than a global rank beside the score). The ranking logic and the scores are
  unchanged; the reports were not regenerated so that the committed outputs stay the ones the
  numbers above describe.

## What two more days would buy

1. **Cross-file reasoning**: a cloned repository and symbol index so a reviewer of `composite.py`
   can see `Options` and the schema editors it changes. The biggest single quality gain.
2. **A real golden set** with human-labelled findings for the three PRs and a dozen more, so
   precision and recall can be measured rather than only groundedness, and the severity bar and
   ranking weights can be tuned against evidence.
3. **Inline posting** behind a human approval step, then **caching** of per-file reviews keyed by
   hunk hash so a re-push only re-reviews what changed.
