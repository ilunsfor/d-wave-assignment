"""Routing: tier selection, unit construction, and that nothing is ever silently dropped."""

import unittest
from pathlib import Path

from reviewer import config, route, triage
from reviewer.schema import FileStat

PINS = Path(__file__).resolve().parent.parent / "pins"


def stat(path, added=10, chars=500, new=False):
    body = "x" * max(chars - 60, 0)
    hunk = f"diff --git a/{path} b/{path}\n@@ -1,1 +1,{added} @@\n+{body}\n"
    return FileStat(path=path, kind=triage.classify(path), added=added, removed=0,
                    is_new=new, is_deleted=False, is_rename=False, hunk_text=hunk)


class TierTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(route.tier_for(config.SMALL_MAX_FILES, config.SMALL_MAX_CHANGED_LINES), "SMALL")
        self.assertEqual(route.tier_for(config.SMALL_MAX_FILES + 1, 10), "MEDIUM")
        self.assertEqual(route.tier_for(2, config.SMALL_MAX_CHANGED_LINES + 1), "MEDIUM")
        self.assertEqual(route.tier_for(config.MEDIUM_MAX_FILES + 1, 10), "LARGE")
        self.assertEqual(route.tier_for(3, config.MEDIUM_MAX_CHANGED_LINES + 1), "LARGE")


class RouteTests(unittest.TestCase):
    def test_small_is_one_unit_with_whole_diff_in_diff_order(self):
        files = [stat("tests/x/tests.py"), stat("django/a.py")]  # test first on purpose
        plan = route.route(files)
        self.assertEqual(plan.tier, "SMALL")
        self.assertEqual(len(plan.units), 1)
        self.assertEqual(plan.units[0].files, ["tests/x/tests.py", "django/a.py"])
        self.assertEqual(plan.units[0].text, files[0].hunk_text + files[1].hunk_text)
        self.assertEqual(plan.skipped, [])

    def test_small_by_lines_but_huge_bytes_falls_to_medium(self):
        files = [stat("django/a.py", added=5, chars=config.UNIT_MAX_CHARS), stat("django/b.py", added=5)]
        plan = route.route(files)
        self.assertEqual(plan.tier, "MEDIUM")
        self.assertEqual(len(plan.units), 2)

    def test_medium_is_one_unit_per_file(self):
        files = [stat(f"django/m{i}.py") for i in range(config.SMALL_MAX_FILES + 1)]
        plan = route.route(files)
        self.assertEqual(plan.tier, "MEDIUM")
        self.assertEqual(len(plan.units), len(files))
        self.assertEqual(sorted(plan.reviewed_paths), sorted(f.path for f in files))

    def test_large_reviews_top_n_and_lists_every_skip(self):
        n = config.MEDIUM_MAX_FILES + 5
        files = [stat(f"django/s{i}.py", added=i + 1) for i in range(n)]
        plan = route.route(files)
        self.assertEqual(plan.tier, "LARGE")
        self.assertEqual(len(plan.units), config.LARGE_TOP_N)
        self.assertEqual(len(plan.skipped), n - config.LARGE_TOP_N)
        # Every file is either reviewed or skipped-with-reason; none vanish.
        self.assertEqual(sorted(plan.reviewed_paths + [s.path for s in plan.skipped]), sorted(f.path for f in files))
        self.assertTrue(all("budget" in s.reason for s in plan.skipped))
        # Highest-scored (largest, since all same kind) files are the ones reviewed.
        self.assertIn(f"django/s{n - 1}.py", plan.reviewed_paths)

    def test_oversized_unit_is_skipped_not_truncated(self):
        files = [stat(f"django/m{i}.py") for i in range(config.SMALL_MAX_FILES + 1)]
        files[0] = stat("django/big.py", chars=config.UNIT_MAX_CHARS + 1)
        plan = route.route(files)
        self.assertEqual([s.path for s in plan.skipped], ["django/big.py"])
        self.assertIn("not truncated", plan.skipped[0].reason)
        self.assertNotIn("django/big.py", plan.reviewed_paths)

    @unittest.skipUnless((PINS / "pr-18056.diff").is_file(), "pins not fetched")
    def test_pinned_tiers(self):
        tiers = {}
        for n in (20426, 19798, 18056):
            files = triage.parse_diff((PINS / f"pr-{n}.diff").read_text(encoding="utf-8"))
            tiers[n] = route.route(files).tier
        self.assertEqual(tiers, {20426: "SMALL", 19798: "SMALL", 18056: "LARGE"})


if __name__ == "__main__":
    unittest.main()
