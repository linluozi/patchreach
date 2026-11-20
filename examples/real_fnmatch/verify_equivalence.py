"""Differential check: the fnmatch fast path must be byte-identical.

Compares the patched ``translate`` in ``work/fnmatch.py`` against the local
stdlib implementation on a fixed-seed corpus of 2,000 unique patterns plus
the real patterns used by the workload.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import random
import sysconfig

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    baseline_path = Path(sysconfig.get_paths()["stdlib"]) / "fnmatch.py"
    patched_path = WORK / "fnmatch.py"
    baseline = load("baseline_fnmatch", baseline_path)
    patched = load("patched_fnmatch", patched_path)

    rng = random.Random(20261121)
    alphabet = list("abcABC012._-/\\*?[]!^-~ ") + ["é", "中", "\t"]
    patterns = set()
    while len(patterns) < 2000:
        length = rng.randint(1, 24)
        patterns.add("".join(rng.choice(alphabet) for _ in range(length)))
    patterns.update(
        [
            "README.md",
            "*.py",
            "data?.csv",
            "[ab]*.txt",
            "file[0-9].log",
            "",
            "***",
            "[]",
            "[!a]",
            "a\\*b",
            "é*.py",
        ]
    )

    unique_inputs = 0
    mismatches = 0
    first_diff = None
    for pattern in sorted(patterns):
        unique_inputs += 1
        left = baseline.translate(pattern)
        right = patched.translate(pattern)
        if left != right:
            mismatches += 1
            if first_diff is None:
                first_diff = (pattern, left, right)
    digest = hashlib.sha256(
        "\n".join(sorted(patterns)).encode("utf-8")
    ).hexdigest()
    print(f"unique_inputs={unique_inputs}")
    print(f"mismatches={mismatches}")
    print(f"corpus_sha256={digest}")
    if first_diff is not None:
        print("first_diff_pattern=%r" % (first_diff[0],))
        print("baseline=%r" % (first_diff[1],))
        print("patched=%r" % (first_diff[2],))
    raise SystemExit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
