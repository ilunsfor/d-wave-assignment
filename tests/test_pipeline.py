"""End-to-end over the pinned #20426 with a fake model: wiring, funnel counts, and report shape."""

import json
import unittest
from pathlib import Path

from reviewer import route, triage
from reviewer.cli import run_review
from reviewer.metrics import Ledger

PINS = Path(__file__).resolve().parent.parent / "pins"
FILE = "django/forms/forms.py"
REAL = '"argument is a dictionary."'


def finding(snippet, severity="should-fix", **over):
    f = {"file": FILE, "line_start": 291, "line_end": 291, "snippet": snippet, "severity": severity,
         "category": "tests", "finding": f"problem with {snippet[:12]}", "suggestion": "fix it",
         "rationale": "because"}
    f.update(over)
    return f


class FakeClient:
    """Answers each phase from a script; records what it was asked."""

    def __init__(self, ledger, review_payload, verdicts):
        self.ledger = ledger
        self.review_payload = review_payload
        self.verdicts = verdicts
        self.prompts: dict[str, list[str]] = {}

    def _log(self, phase, user):
        self.prompts.setdefault(phase, []).append(user)
        self.ledger.record_call(phase, "fake-model", 100, 10, 0.01, 1)

    def json(self, *, phase, model, system, user, schema):
        self._log(phase, user)
        if phase == "review":
            return self.review_payload
        if phase == "verify":
            return {"verdicts": [{"index": i, "verdict": v, "reason": "r"} for i, v in enumerate(self.verdicts)]}
        raise AssertionError(phase)

    def text(self, *, phase, model, system, user):
        self._log(phase, user)
        if phase == "triage":
            return "Rewords an error message."
        if phase == "synth":
            return "Assessment.\n\n## Should fix\n\n- **`django/forms/forms.py:291-291`** ok"
        raise AssertionError(phase)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.files = triage.parse_diff((PINS / "pr-20426.diff").read_text(encoding="utf-8"))
        self.plan = route.route(self.files)

    def run_with(self, payload, verdicts, nits=False):
        ledger = Ledger(label="pr-20426")
        client = FakeClient(ledger, payload, verdicts)
        md = run_review("pr-20426", self.files, self.plan, client, ledger, emit_nits=nits)
        return md, ledger, client

    def test_zero_findings_means_no_synth_call_and_short_output(self):
        md, ledger, client = self.run_with({"findings": []}, [])
        self.assertIn("No blocking or should-fix findings.", md)
        self.assertNotIn("synth", client.prompts)
        self.assertNotIn("verify", client.prompts)  # nothing to verify
        self.assertEqual(ledger.counts["findings_emitted"], 0)
        self.assertNotIn("## Not reviewed", md)  # SMALL tier skipped nothing

    def test_funnel_counts_and_filters(self):
        # review() sorts findings by (file, line, text), so the finding texts are numbered to fix
        # the order the fake verifier's verdict list is applied in.
        payload = {"findings": [
            finding(REAL, finding="1 real"),                        # supported, grounded -> emitted
            finding(REAL, finding="2 dup"),                         # unsupported -> rejected
            finding('"argument is a dict."', finding="3 para"),     # supported but paraphrased -> ungrounded
            finding(REAL, severity="nit", finding="4 nit"),         # nit -> suppressed before verify
            {"file": FILE, "severity": "should-fix"},               # malformed -> dropped at parse
            finding(REAL, file="not/in/diff.py", finding="5 zz"),   # file not in diff -> malformed
        ]}
        md, ledger, client = self.run_with(payload, verdicts=["supported", "unsupported", "supported"])
        c = ledger.counts
        self.assertEqual(c["findings_drafted"], 6)
        self.assertEqual(c["findings_malformed"], 2)
        self.assertEqual(c["findings_suppressed"], 1)
        self.assertEqual(c["findings_verified"], 3)
        self.assertEqual(c["findings_rejected"], 1)
        self.assertEqual(c["findings_ungrounded"], 1)
        self.assertEqual(c["findings_emitted"], 1)
        self.assertEqual(c["groundedness"], {"grounded": 1, "total": 2, "rate": 0.5})
        self.assertEqual(c["verifier_rejection_rate"], round(1 / 3, 3))
        self.assertIn("## Should fix", md)
        self.assertIn("groundedness 1/2", md)

    def test_verifier_sees_claims_but_not_rationale(self):
        payload = {"findings": [finding(REAL, rationale="SECRET-REASONING")]}
        _, _, client = self.run_with(payload, verdicts=["supported"])
        verify_prompt = client.prompts["verify"][0]
        self.assertIn(REAL, verify_prompt)
        self.assertIn("claim:", verify_prompt)
        self.assertNotIn("SECRET-REASONING", verify_prompt)
        self.assertNotIn("SECRET-REASONING", client.prompts["synth"][0])

    def test_synth_omission_is_repaired(self):
        payload = {"findings": [finding(REAL), finding("raise TypeError(", line_start=289, line_end=289, finding="q")]}
        md, ledger, _ = self.run_with(payload, verdicts=["supported", "supported"])
        # Fake synth only mentions line 291; the 289 finding must be appended, not lost.
        self.assertIn("django/forms/forms.py:289", md)
        self.assertTrue(any("omitted 1 finding" in n for n in ledger.notes))

    def test_diff_enters_prompts_only_as_labelled_data(self):
        _, _, client = self.run_with({"findings": [finding(REAL)]}, ["supported"])
        for phase in ("triage", "review", "verify"):
            for prompt in client.prompts[phase]:
                self.assertIn("untrusted data", prompt, phase)

    def test_triage_summariser_sees_the_diff_markers(self):
        # Without the +/- markers the removed line and its replacement read as consecutive code,
        # and the first live runs' summariser duly reported a defect that did not exist.
        _, _, client = self.run_with({"findings": []}, [])
        prompt = client.prompts["triage"][0]
        self.assertIn('-                    "argument contains errors for multiple fields."', prompt)
        self.assertIn('+                    "argument is a dictionary."', prompt)
        self.assertIn("@@", prompt)

    def test_metrics_report_is_json_serialisable(self):
        _, ledger, _ = self.run_with({"findings": [finding(REAL)]}, ["supported"])
        r = json.loads(json.dumps(ledger.report()))
        self.assertEqual(r["tier"], "SMALL")
        self.assertEqual(r["files_reviewed"], 2)
        self.assertIn("review", r["tokens"]["by_phase"])
        self.assertEqual(r["cost"]["unpriced_models"], ["fake-model"])


if __name__ == "__main__":
    unittest.main()
