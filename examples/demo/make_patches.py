"""Generate the demo patches deterministically with difflib."""

from __future__ import annotations

import difflib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def write_patch(name: str, old: str, new: str, label: str) -> None:
    diff = difflib.unified_diff(
        old.splitlines(True),
        new.splitlines(True),
        fromfile="pkg/impl.py",
        tofile="pkg/impl.py",
        lineterm="\n",
    )
    text = "".join(diff)
    (HERE / name).write_text(text, encoding="utf-8")
    print(f"{label}: wrote {name} ({len(text.splitlines())} lines)")


def main() -> None:
    baseline = (HERE / "baseline" / "pkg" / "impl.py").read_text(encoding="utf-8")
    patched = (HERE / "pkg" / "impl.py").read_text(encoding="utf-8")
    # The cold patch is relative to the *full* patched file, so its new-side
    # line numbers line up with pkg/impl.py.  It changes only normalize().
    cold_old = (HERE / "cold" / "pkg" / "impl.py").read_text(encoding="utf-8")
    write_patch("patch.diff", baseline, patched, "full patch")
    write_patch("patch_cold.diff", cold_old, patched, "cold-only patch")


if __name__ == "__main__":
    main()
