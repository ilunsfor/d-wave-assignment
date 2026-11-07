"""Diff parsing and file classification. Run: python -m unittest discover -s tests"""

import unittest
from pathlib import Path

from reviewer import triage

PINS = Path(__file__).resolve().parent.parent / "pins"

SYNTHETIC = """\
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
diff --git a/tests/forms_tests/tests/test_forms.py b/tests/forms_tests/tests/test_forms.py
index 8cc1fecf0a17..7f698ad706cd 100644
--- a/tests/forms_tests/tests/test_forms.py
+++ b/tests/forms_tests/tests/test_forms.py
@@ -1660,4 +1660,7 @@ def clean(self):
         with self.assertRaisesMessage(ValueError, "has no field named"):
             f.add_error("missing_field", "Some error.")

+        with self.assertRaisesMessage(TypeError, "is a dictionary"):
+            f.add_error("password1", ValidationError({"password1": "Some error."}))
+
     def test_update_error_dict(self):
diff --git a/docs/releases/5.2.txt b/docs/releases/5.2.txt
new file mode 100644
index 000000000000..0123456789ab
--- /dev/null
+++ b/docs/releases/5.2.txt
@@ -0,0 +1,2 @@
+Composite primary keys
+----------------------
"""


class ParseTests(unittest.TestCase):
    def test_splits_files_and_counts_lines(self):
        files = triage.parse_diff(SYNTHETIC)
        self.assertEqual([f.path for f in files],
                         ["django/forms/forms.py", "tests/forms_tests/tests/test_forms.py", "docs/releases/5.2.txt"])
        self.assertEqual((files[0].added, files[0].removed), (1, 1))
        self.assertEqual((files[1].added, files[1].removed), (3, 0))
        self.assertTrue(files[2].is_new)
        self.assertFalse(files[0].is_new)

    def test_chunk_text_is_verbatim(self):
        files = triage.parse_diff(SYNTHETIC)
        self.assertTrue(files[0].hunk_text.startswith("diff --git a/django/forms/forms.py"))
        self.assertIn('+                    "argument is a dictionary."', files[0].hunk_text)
        # Chunks partition the input exactly.
        self.assertEqual("".join(f.hunk_text for f in files), SYNTHETIC)

    def test_hunk_code_text_strips_prefixes_only(self):
        files = triage.parse_diff(SYNTHETIC)
        code = triage.hunk_code_text(files[0].hunk_text)
        self.assertIn('                    "argument is a dictionary."', code)  # indentation kept
        self.assertIn('                    "argument contains errors for multiple fields."', code)  # removed lines too
        self.assertNotIn("@@", code)
        self.assertNotIn("diff --git", code)

    def test_rejects_non_diff(self):
        with self.assertRaises(triage.ParseError):
            triage.parse_diff("hello world\n")

    @unittest.skipUnless((PINS / "pr-18056.diff").is_file(), "pins not fetched")
    def test_pinned_18056_shape(self):
        files = triage.parse_diff((PINS / "pr-18056.diff").read_text(encoding="utf-8"))
        t = triage.totals(files)
        self.assertEqual(t["files"], 43)
        self.assertEqual((t["source_files"], t["test_files"], t["docs_files"]), (17, 21, 5))
        self.assertEqual(sum(1 for f in files if f.is_new), 17)


class ClassifyTests(unittest.TestCase):
    def test_kinds(self):
        cases = {
            "django/db/models/base.py": "source",
            "django/test/testcases.py": "source",  # the test framework is source
            "tests/foreign_object/tests.py": "test",
            "tests/fixtures/data.json": "test",
            "django/contrib/admin/tests.py": "test",
            "docs/releases/5.2.txt": "docs",
            "README.rst": "docs",
            "pyproject.toml": "config",
            "tox.ini": "config",
            ".pre-commit-config.yaml": "config",
        }
        for path, kind in cases.items():
            with self.subTest(path=path):
                self.assertEqual(triage.classify(path), kind)


if __name__ == "__main__":
    unittest.main()
