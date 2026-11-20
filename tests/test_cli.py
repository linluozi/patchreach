"""End-to-end tests for the CLI and the bundled examples."""

import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "demo"


def run_cli(args, timeout=90):
    return subprocess.run(
        [sys.executable, "-m", "patchreach", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run(
            [sys.executable, str(DEMO / "make_patches.py")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_squares_gate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "report.json")
            cp = run_cli(
                [
                    "check",
                    "--patch",
                    "examples/demo/patch.diff",
                    "--root",
                    "examples/demo",
                    "--script",
                    "workload_squares.py",
                    "--json",
                    out,
                ]
            )
            self.assertEqual(cp.returncode, 1, cp.stderr)
            with open(out, encoding="utf-8") as fh:
                report = json.load(fh)
            self.assertEqual(report["gate"], "fail")
            self.assertEqual(report["totals"]["executable_lines"], 4)
            self.assertEqual(report["totals"]["reached_lines"], 2)
            self.assertEqual(
                {row["function"] for row in report["cold_lines"]}, {"normalize"}
            )

    def test_both_gate_passes(self):
        cp = run_cli(
            [
                "check",
                "--patch",
                "examples/demo/patch.diff",
                "--root",
                "examples/demo",
                "--script",
                "workload_both.py",
            ]
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("GATE: PASS", cp.stdout)

    def test_cold_claim_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "report.json")
            cp = run_cli(
                [
                    "check",
                    "--patch",
                    "examples/demo/patch_cold.diff",
                    "--root",
                    "examples/demo",
                    "--script",
                    "workload_squares.py",
                    "--time",
                    "--claim",
                    "1.2",
                    "--min-reach",
                    "0",
                    "--min-hot",
                    "0",
                    "--json",
                    out,
                ]
            )
            self.assertEqual(cp.returncode, 1, cp.stderr)
            with open(out, encoding="utf-8") as fh:
                report = json.load(fh)
            self.assertIn("claim", report["reason"])
            self.assertEqual(report["time_ceiling"]["ceiling_upper"], 1.0)

    def test_explain(self):
        cp = run_cli(
            ["explain", "--patch", "examples/demo/patch.diff", "--root", "examples/demo"]
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("exec", cp.stdout)
        self.assertIn("non-exec", cp.stdout)

    def test_missing_workload_is_usage_error(self):
        cp = run_cli(
            ["check", "--patch", "examples/demo/patch.diff", "--root", "examples/demo"]
        )
        self.assertEqual(cp.returncode, 2)

    def test_real_fnmatch_positive_control(self):
        stdlib = Path(sysconfig.get_paths()["stdlib"]) / "fnmatch.py"
        if not stdlib.exists():
            self.skipTest("stdlib fnmatch.py not found")
        subprocess.run(
            [sys.executable, str(ROOT / "examples" / "real_fnmatch" / "make_fixture.py")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        cp = run_cli(
            [
                "check",
                "--patch",
                "examples/real_fnmatch/work/patch.diff",
                "--root",
                "examples/real_fnmatch/work",
                "--script",
                "workload.py",
                "--min-reach",
                "1.0",
                "--min-hot",
                "1.0",
            ]
        )
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIn("GATE: PASS", cp.stdout)


if __name__ == "__main__":
    unittest.main()
