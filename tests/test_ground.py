"""Groundedness is exact. A snippet that is almost in the diff is not in the diff."""

import unittest

from reviewer import verify
from reviewer.schema import Finding
from reviewer.triage import parse_diff

DIFF = """\
diff --git a/django/forms/forms.py b/django/forms/forms.py
index 760ba7b76792..ce64f6286e57 100644
--- a/django/forms/forms.py
+++ b/django/forms/forms.py
@@ -288,7 +288,7 @@ def add_error(self, field, error):
             if field is not None:
                 raise TypeError(
                     "The argument `field` must be `None` when the `error` "
-                    "argument contains errors for multiple fields."
+                    "argument is a dictionary."
                 )
             else:
                 error = error.error_dict
"""


def finding(snippet, file="django/forms/forms.py", severity="should-fix", verdict="supported"):
    return Finding(file=file, line_start=291, line_end=291, snippet=snippet, severity=severity,
                   category="correctness", finding="x", suggestion="y", verdict=verdict)


class GroundTests(unittest.TestCase):
    def setUp(self):
        self.files = {f.path: f for f in parse_diff(DIFF)}

    def ground(self, *findings):
        kept, dropped = verify.ground(list(findings), self.files)
        return kept, dropped

    def test_added_line_grounded(self):
        kept, dropped = self.ground(finding('"argument is a dictionary."'))
        self.assertEqual((len(kept), dropped), (1, 0))
        self.assertTrue(kept[0].grounded)

    def test_removed_and_context_lines_are_in_the_diff_too(self):
        kept, dropped = self.ground(
            finding('"argument contains errors for multiple fields."'),
            finding("raise TypeError("),
        )
        self.assertEqual((len(kept), dropped), (2, 0))

    def test_multiline_snippet_with_indentation(self):
        snippet = '                raise TypeError(\n                    "The argument `field` must be `None` when the `error` "'
        kept, dropped = self.ground(finding(snippet))
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_whitespace_is_not_normalised(self):
        # Same tokens, collapsed indentation: NOT in the diff. This is the case a lenient check
        # would wave through, and the one that lets a model cite code that is not there.
        snippet = 'raise TypeError(\n"The argument `field` must be `None` when the `error` "'
        kept, dropped = self.ground(finding(snippet))
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_paraphrase_is_ungrounded(self):
        kept, dropped = self.ground(finding('"argument is a dict."'))
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_prefix_characters_are_not_part_of_the_code(self):
        # The model is told to omit +/-; including one means the quote is of the diff, not the code.
        kept, dropped = self.ground(finding('+                    "argument is a dictionary."'))
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_wrong_file_is_ungrounded(self):
        kept, dropped = self.ground(finding('"argument is a dictionary."', file="tests/forms_tests/tests/test_forms.py"))
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_case_matters(self):
        kept, dropped = self.ground(finding('"Argument is a dictionary."'))
        self.assertEqual((len(kept), dropped), (0, 1))

    def test_quote_of_new_code_across_the_changed_line_is_grounded(self):
        # Context line followed directly by the added line: contiguous in the after-image, but
        # the removed line sits between them in the raw hunk. The first live run on #20426
        # dropped exactly this (correct) quote as ungrounded.
        snippet = ('                    "The argument `field` must be `None` when the `error` "\n'
                   '                    "argument is a dictionary."')
        kept, dropped = self.ground(finding(snippet))
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_quote_of_old_code_across_the_changed_line_is_grounded(self):
        snippet = ('                    "The argument `field` must be `None` when the `error` "\n'
                   '                    "argument contains errors for multiple fields."')
        kept, dropped = self.ground(finding(snippet))
        self.assertEqual((len(kept), dropped), (1, 0))

    def test_removed_line_followed_by_added_line_is_a_quote_of_the_diff_not_the_code(self):
        snippet = ('                    "argument contains errors for multiple fields."\n'
                   '                    "argument is a dictionary."')
        kept, dropped = self.ground(finding(snippet))
        self.assertEqual((len(kept), dropped), (0, 1))


class VerdictPolicyTests(unittest.TestCase):
    def test_supported_kept_unsupported_dropped(self):
        kept, rejected = verify.apply_verdicts([finding("a", verdict="supported"), finding("b", verdict="unsupported")])
        self.assertEqual(([f.snippet for f in kept], rejected), (["a"], 1))

    def test_uncertain_kept_only_when_blocking(self):
        kept, rejected = verify.apply_verdicts([
            finding("a", severity="blocking", verdict="uncertain"),
            finding("b", severity="should-fix", verdict="uncertain"),
        ])
        self.assertEqual(([f.snippet for f in kept], rejected), (["a"], 1))
        self.assertEqual(kept[0].verdict, "uncertain")  # still marked, so the report says "unverified"


if __name__ == "__main__":
    unittest.main()
