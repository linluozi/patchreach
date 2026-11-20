"""Build a real-code fixture from the local CPython stdlib fnmatch module.

The patch adds a literal fast path to ``translate``: a pattern with no ``*``,
``?`` or ``[`` can be escaped in one call instead of walking it character by
character.  ``verify_equivalence.py`` checks the fast path byte-for-byte
against the unpatched function.
"""

from __future__ import annotations

import difflib
from pathlib import Path
import sysconfig

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"

FAST_PATH = (
    "    if not any(ch in pat for ch in '*?['):\n"
    "        return fr'(?s:{re.escape(pat)})\\Z'\n"
)


def main() -> None:
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    source_path = stdlib / "fnmatch.py"
    baseline = source_path.read_text(encoding="utf-8")
    anchor = "    STAR = object()\n"
    if anchor not in baseline:
        raise SystemExit(f"anchor not found in {source_path}")
    if FAST_PATH.strip() in baseline:
        raise SystemExit("fixture already patched; delete work/ and retry")
    patched = baseline.replace(anchor, FAST_PATH + anchor, 1)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "fnmatch.py").write_text(patched, encoding="utf-8")
    # Copy the workload next to the fixture so --root WORK --script workload.py
    # resolves without depending on the caller's cwd.
    workload = (HERE / "workload.py").read_text(encoding="utf-8")
    (WORK / "workload.py").write_text(workload, encoding="utf-8")
    diff = difflib.unified_diff(
        baseline.splitlines(True),
        patched.splitlines(True),
        fromfile="fnmatch.py",
        tofile="fnmatch.py",
        lineterm="\n",
    )
    (WORK / "patch.diff").write_text("".join(diff), encoding="utf-8")
    print(f"source: {source_path}")
    print(f"wrote: {WORK / 'fnmatch.py'}")
    print(f"wrote: {WORK / 'patch.diff'}")


if __name__ == "__main__":
    main()
