"""Ranking: kind dominates, hot paths and new files add, size is only a tiebreak."""

import unittest
from pathlib import Path

from reviewer import config, triage
from reviewer.schema import FileStat

PINS = Path(__file__).resolve().parent.parent / "pins"


def stat(path, added=10, removed=0, new=False, deleted=False):
    return FileStat(path=path, kind=triage.classify(path), added=added, removed=removed,
                    is_new=new, is_deleted=deleted, is_rename=False, hunk_text="")


class RankTests(unittest.TestCase):
    def test_kind_outranks_size(self):
        small_source = stat("django/db/models/fields/composite.py", added=5)
        huge_test = stat("tests/composite_pk/test_filter.py", added=2000)
        huge_docs = stat("docs/topics/composite-primary-key.txt", added=2000)
        ranked = triage.rank([huge_docs, huge_test, small_source])
        self.assertEqual([f.path for f in ranked],
                         [small_source.path, huge_test.path, huge_docs.path])

    def test_size_bonus_is_capped(self):
        a = stat("django/a.py", added=config.SIZE_DIVISOR * config.SIZE_BONUS_CAP)
        b = stat("django/b.py", added=config.SIZE_DIVISOR * config.SIZE_BONUS_CAP * 10)
        self.assertEqual(triage.score(a), triage.score(b))

    def test_hot_path_and_new_file_bonuses(self):
        plain = stat("django/db/models/fields/composite.py")
        hot = stat("django/db/backends/base/schema.py")
        new = stat("django/db/models/fields/composite.py", new=True)
        self.assertEqual(triage.score(hot) - triage.score(plain), config.HOT_PATH_BONUS)
        self.assertEqual(triage.score(new) - triage.score(plain), config.NEW_FILE_BONUS)

    def test_deleted_file_sinks(self):
        kept = stat("django/x.py")
        gone = stat("django/y.py", removed=10, added=0, deleted=True)
        self.assertLess(triage.score(gone), triage.score(kept))

    def test_ties_break_by_changed_then_path(self):
        a = stat("django/b.py", added=3)
        b = stat("django/a.py", added=3)
        c = stat("django/c.py", added=4)
        self.assertEqual([f.path for f in triage.rank([a, b, c])], ["django/c.py", "django/a.py", "django/b.py"])

    @unittest.skipUnless((PINS / "pr-18056.diff").is_file(), "pins not fetched")
    def test_pinned_18056_top_n_is_all_source(self):
        files = triage.parse_diff((PINS / "pr-18056.diff").read_text(encoding="utf-8"))
        top = triage.rank(files)[: config.LARGE_TOP_N]
        self.assertTrue(all(f.kind == "source" for f in top), [f.path for f in top])
        self.assertIn("django/db/models/fields/composite.py", [f.path for f in top])


if __name__ == "__main__":
    unittest.main()
