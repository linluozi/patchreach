"""Unit tests for the certificate builder."""

import json
import os
import tempfile
import unittest

from patchreach.diffparse import ChangedFile, DiffResult
from patchreach.report import build_report

SOURCE = '''def hot(values):
    total = 0
    for value in values:
        total += value
    return total


def cold(text):
    return text.strip()
'''


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "pkg"))
        with open(os.path.join(self.root, "pkg", "impl.py"), "w", encoding="utf-8") as fh:
            fh.write(SOURCE)
        self.diff = DiffResult()
        self.diff.files["pkg/impl.py"] = ChangedFile(
            path="pkg/impl.py", added=[2, 4, 9], hunks=2
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_pass_when_changed_lines_are_hot(self):
        hits = {"pkg/impl.py:2": 50, "pkg/impl.py:4": 50, "pkg/impl.py:9": 50}
        report = build_report(self.diff, hits, self.root, min_reach=0.8, min_hot=0.5)
        self.assertTrue(report["passed"])
        self.assertEqual(report["gate"], "pass")
        self.assertAlmostEqual(report["totals"]["reach"], 1.0)
        self.assertAlmostEqual(report["totals"]["hot_fraction"], 1.0)

    def test_fail_when_a_changed_function_is_cold(self):
        hits = {"pkg/impl.py:2": 50, "pkg/impl.py:4": 50, "pkg/impl.py:9": 0}
        report = build_report(self.diff, hits, self.root, min_reach=0.8, min_hot=0.5)
        self.assertFalse(report["passed"])
        self.assertEqual(report["gate"], "fail")
        self.assertIn("reach", report["reason"])
        self.assertEqual(
            report["cold_lines"],
            [{"file": "pkg/impl.py", "line": 9, "function": "cold"}],
        )

    def test_non_executable_changed_lines_are_excluded(self):
        diff = DiffResult()
        # Line 6 is blank in SOURCE; line 3 is the for-loop.
        diff.files["pkg/impl.py"] = ChangedFile(path="pkg/impl.py", added=[6, 3])
        hits = {"pkg/impl.py:6": 0, "pkg/impl.py:3": 20}
        report = build_report(diff, hits, self.root, min_reach=1.0, min_hot=1.0)
        self.assertEqual(report["totals"]["added_lines"], 2)
        self.assertEqual(report["totals"]["executable_lines"], 1)
        self.assertEqual(report["totals"]["non_executable_lines"], 1)
        self.assertTrue(report["passed"])

    def test_claim_ceiling_rejects_impossible_speedup(self):
        profile = {
            "total_time": 10.0,
            "entries": [
                {
                    "file": "pkg/impl.py",
                    "line": 1,
                    "name": "hot",
                    "ncalls": 1,
                    "tottime": 1.0,
                    "cumtime": 8.0,
                }
            ],
        }
        hits = {"pkg/impl.py:2": 10, "pkg/impl.py:4": 10}
        # Only the `hot` function is in the diff (lines 2 and 4).
        diff = DiffResult()
        diff.files["pkg/impl.py"] = ChangedFile(path="pkg/impl.py", added=[2, 4])
        report = build_report(
            diff,
            hits,
            self.root,
            profile=profile,
            min_reach=0.0,
            min_hot=0.0,
            claim=6.0,
        )
        self.assertFalse(report["passed"])
        self.assertIn("claim", report["reason"])
        self.assertAlmostEqual(report["time_ceiling"]["cumtime_share"], 0.8)
        self.assertAlmostEqual(report["time_ceiling"]["ceiling_upper"], 5.0)

    def test_cold_changed_function_has_ceiling_one(self):
        profile = {
            "total_time": 10.0,
            "entries": [
                {
                    "file": "pkg/impl.py",
                    "line": 1,
                    "name": "hot",
                    "ncalls": 1,
                    "tottime": 9.0,
                    "cumtime": 9.0,
                }
            ],
        }
        diff = DiffResult()
        diff.files["pkg/impl.py"] = ChangedFile(path="pkg/impl.py", added=[9])
        report = build_report(
            diff,
            {},
            self.root,
            profile=profile,
            min_reach=0.0,
            min_hot=0.0,
            claim=1.2,
        )
        self.assertFalse(report["passed"])
        self.assertEqual(report["time_ceiling"]["ceiling_upper"], 1.0)
        self.assertIn("claim", report["reason"])

    def test_digest_is_deterministic(self):
        hits = {"pkg/impl.py:2": 3, "pkg/impl.py:4": 3, "pkg/impl.py:9": 3}
        first = build_report(self.diff, hits, self.root)
        second = build_report(
            self.diff, hits, self.root, elapsed_s=9.99, events=123456
        )
        self.assertEqual(first["certificate_sha256"], second["certificate_sha256"])
        # The digest is over the canonical report without volatile fields.
        body = dict(first)
        digest = body.pop("certificate_sha256")
        body.pop("elapsed_s")
        body.pop("events")
        body.pop("time_ceiling")
        import hashlib

        canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(digest, hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    def test_error_gate(self):
        hits = {"pkg/impl.py:2": 1, "pkg/impl.py:4": 1, "pkg/impl.py:9": 1}
        report = build_report(
            self.diff,
            hits,
            self.root,
            error="Traceback: boom",
            workload_exit=1,
        )
        self.assertFalse(report["passed"])
        self.assertEqual(report["gate"], "error")

    def test_truncated_is_inconclusive(self):
        hits = {"pkg/impl.py:2": 1, "pkg/impl.py:4": 1, "pkg/impl.py:9": 1}
        report = build_report(self.diff, hits, self.root, truncated=True, events=10)
        self.assertFalse(report["passed"])
        self.assertEqual(report["gate"], "inconclusive")

    def test_non_python_lines_are_skipped(self):
        with open(os.path.join(self.root, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("<!-- not valid Python -->\n")
        diff = DiffResult()
        diff.files["README.md"] = ChangedFile(path="README.md", added=[1])
        report = build_report(diff, {}, self.root)
        self.assertEqual(report["totals"]["skipped_non_python_lines"], 1)
        self.assertEqual(report["totals"]["executable_lines"], 0)
        self.assertEqual(report["gate"], "not-applicable")

    def test_uncompilable_python_is_inconclusive(self):
        with open(os.path.join(self.root, "pkg", "bad.py"), "w", encoding="utf-8") as fh:
            fh.write("def f(:\n    pass\n")
        diff = DiffResult()
        diff.files["pkg/bad.py"] = ChangedFile(path="pkg/bad.py", added=[1])
        report = build_report(diff, {}, self.root, min_reach=0.0, min_hot=0.0)
        self.assertEqual(report["totals"]["unknown_python_lines"], 1)
        self.assertEqual(report["gate"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
