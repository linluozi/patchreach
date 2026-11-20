"""Static helpers: which changed lines can emit a LINE event, and where.

A comment or a blank line can appear in a diff but can never be executed, so
it must not sit in the denominator of a reach score.  We compile the patched
file and read ``code.co_lines()`` to obtain the exact set of lines the
interpreter can report.  Function spans come from ``ast`` and are used both
for human-readable grouping and for the profiler-based ceiling.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
import types

__all__ = ["FunctionSpan", "executable_lines", "function_spans", "function_for_line"]


@dataclass
class FunctionSpan:
    name: str            # qualified display name
    co_name: str         # ast identifier; matches code.co_name
    def_line: int
    end_line: int
    file: str = ""
    parent: "FunctionSpan | None" = None
    children: list["FunctionSpan"] = field(default_factory=list)

    def contains(self, line: int) -> bool:
        return self.def_line <= line <= self.end_line

    def path(self) -> list["FunctionSpan"]:
        out = []
        node: FunctionSpan | None = self
        while node is not None:
            out.append(node)
            node = node.parent
        return list(reversed(out))


def executable_lines(source: str, filename: str = "<patch>") -> set[int] | None:
    """Return the set of source lines that can emit a ``LINE`` event.

    Returns ``None`` when the file cannot be compiled (e.g. it is not Python,
    or the patch does not apply cleanly).  Callers must treat ``None`` as
    "unknown" rather than "empty".
    """
    try:
        code = compile(source, filename, "exec")
    except (SyntaxError, ValueError):
        return None
    lines: set[int] = set()
    stack: list[types.CodeType] = [code]
    while stack:
        current = stack.pop()
        try:
            for _start, _end, line in current.co_lines():
                if line is not None:
                    lines.add(line)
        except AttributeError:  # pragma: no cover - very old interpreters
            pass
        for const in current.co_consts:
            if isinstance(const, types.CodeType):
                stack.append(const)
    return lines


def function_spans(source: str, filename: str = "<patch>") -> list[FunctionSpan]:
    """Return every function/lambda span in ``source``, outermost first."""
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError):
        return []

    spans: list[FunctionSpan] = []

    def walk(node: ast.AST, parent: FunctionSpan | None, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                if isinstance(child, ast.Lambda):
                    name = "<lambda>"
                    co_name = "<lambda>"
                    def_line = child.lineno
                    end_line = getattr(child, "end_lineno", def_line) or def_line
                else:
                    name = child.name
                    co_name = child.name
                    def_line = child.lineno
                    end_line = getattr(child, "end_lineno", def_line) or def_line
                display = f"{prefix}{name}"
                span = FunctionSpan(
                    name=display,
                    co_name=co_name,
                    def_line=def_line,
                    end_line=end_line,
                    file=filename,
                    parent=parent,
                )
                if parent is not None:
                    parent.children.append(span)
                spans.append(span)
                walk(child, span, display + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, parent, prefix + child.name + ".")
            else:
                walk(child, parent, prefix)

    walk(tree, None, "")
    return spans


def function_for_line(spans: list[FunctionSpan], line: int) -> FunctionSpan | None:
    """Return the innermost span containing ``line`` (largest ``def_line``)."""
    best: FunctionSpan | None = None
    for span in spans:
        if span.contains(line) and (best is None or span.def_line > best.def_line):
            best = span
    return best
