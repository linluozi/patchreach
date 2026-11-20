"""Parse unified diffs into changed new-file line numbers.

Only the *new* side matters for reach: a line that a patch adds or rewrites is
the line whose execution we want to certify.  Pure deletions have no new line
to execute, so they are counted but never scored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

__all__ = [
    "ChangedFile",
    "DiffResult",
    "DiffParseError",
    "parse_unified_diff",
]

_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?: ?(?P<context>.*))?$"
)


class DiffParseError(ValueError):
    """Raised when a file claims to be a unified diff but has no hunks."""


@dataclass
class ChangedFile:
    """One file mentioned by a unified diff."""

    path: str
    added: list[int] = field(default_factory=list)
    removed: int = 0
    hunks: int = 0
    is_new: bool = False
    is_deleted: bool = False

    @property
    def added_count(self) -> int:
        return len(self.added)


@dataclass
class DiffResult:
    """Parsed diff, keyed by the new-side path (POSIX separators)."""

    files: dict[str, ChangedFile] = field(default_factory=dict)

    @property
    def added_lines(self) -> int:
        return sum(len(f.added) for f in self.files.values())

    @property
    def removed_lines(self) -> int:
        return sum(f.removed for f in self.files.values())

    def paths(self) -> list[str]:
        return sorted(self.files)


def _unquote(path: str) -> str:
    """Undo git's C-style quoting for paths with spaces or unusual bytes."""
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        body = path[1:-1]
        out = []
        i = 0
        while i < len(body):
            c = body[i]
            if c == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                simple = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}
                if nxt in simple:
                    out.append(simple[nxt])
                    i += 2
                    continue
                # Octal escapes (e.g. \303\251); git uses three digits.
                if nxt in "01234567" and i + 3 < len(body):
                    try:
                        out.append(chr(int(body[i + 1 : i + 4], 8)))
                        i += 4
                        continue
                    except ValueError:
                        pass
            out.append(c)
            i += 1
        return "".join(out)
    return path


def _strip_prefix(path: str) -> str:
    """Drop the ``a/`` or ``b/`` prefix git adds, and normalise separators."""
    path = _unquote(path.strip())
    # Timestamps in plain ``diff -u`` output are tab-separated.
    if "\t" in path:
        path = path.split("\t", 1)[0]
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path.replace("\\", "/")


def parse_unified_diff(text: str) -> DiffResult:
    """Parse a unified diff (git or plain ``diff -u``).

    The returned line numbers are 1-based positions in the *new* file.  A
    hunk header with an omitted count means a count of 1, per the format.
    """
    result = DiffResult()
    current: ChangedFile | None = None
    old_path: str | None = None
    new_path: str | None = None
    is_new_pending = False
    is_deleted_pending = False
    old_line = 0
    new_line = 0
    in_hunk = False
    saw_hunk = False

    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("diff --git "):
            # A new file section; keep the previous ChangedFile in the result.
            current = None
            in_hunk = False
            old_path = new_path = None
            is_new_pending = is_deleted_pending = False
            continue
        if line.startswith("--- "):
            old_path = _strip_prefix(line[4:])
            is_new_pending = old_path == "/dev/null"
            if is_new_pending:
                old_path = None
            in_hunk = False
            continue
        if line.startswith("+++ "):
            new_path = _strip_prefix(line[4:])
            is_deleted_pending = new_path == "/dev/null"
            if is_deleted_pending:
                # Keep the old path so a deleted file is still reported.
                new_path = old_path or "<deleted>"
            if new_path is None:
                raise DiffParseError("+++ line before --- line")
            current = result.files.setdefault(new_path, ChangedFile(new_path))
            current.is_new = current.is_new or is_new_pending
            current.is_deleted = current.is_deleted or is_deleted_pending
            in_hunk = False
            continue
        if line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if not m:
                raise DiffParseError(f"malformed hunk header: {line!r}")
            if current is None:
                raise DiffParseError(f"hunk before file header: {line!r}")
            old_line = int(m.group("old_start"))
            new_line = int(m.group("new_start"))
            current.hunks += 1
            saw_hunk = True
            in_hunk = True
            continue
        if not in_hunk or current is None:
            continue
        if line.startswith("\\"):
            # "\ No newline at end of file" — no line number advance.
            continue
        if line.startswith("+"):
            current.added.append(new_line)
            new_line += 1
        elif line.startswith("-"):
            current.removed += 1
            old_line += 1
        elif line.startswith(" "):
            old_line += 1
            new_line += 1
        else:
            # Empty line inside a hunk is a context line in some producers.
            old_line += 1
            new_line += 1

    for cf in result.files.values():
        # De-duplicate defensively; a hunk should never repeat a new line.
        cf.added = sorted(set(cf.added))

    if not saw_hunk and text.strip():
        # A diff with no hunks is legal (e.g. mode-only change), but we want
        # callers to notice that there is nothing to trace.
        return result
    if not result.files and text.strip():
        raise DiffParseError("no file headers found; is this a unified diff?")
    return result


def read_patch(path: str) -> DiffResult:
    """Read and parse a patch file from disk."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_unified_diff(fh.read())
