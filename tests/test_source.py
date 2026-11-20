"""Unit tests for executable-line and function-span analysis."""

import unittest

from patchreach.source import (
    executable_lines,
    function_for_line,
    function_spans,
)

SOURCE = '''"""module docstring"""

# a comment
VALUE = 1


def outer(a):
    # inner comment

    def inner(b):
        return b + 1

    return inner(a) + VALUE


class Thing:
    def method(self):
        return 2


lam = lambda x: x * 2
'''


def line_of(needle: str) -> int:
    return SOURCE.splitlines().index(needle) + 1


class SourceTests(unittest.TestCase):
    def test_executable_excludes_comments_and_blanks(self):
        lines = executable_lines(SOURCE, "m.py")
        self.assertIsNotNone(lines)
        self.assertNotIn(line_of("# a comment"), lines)
        self.assertNotIn(line_of(""), lines)
        # The assignment, return statements and class body are executable.
        self.assertIn(line_of("VALUE = 1"), lines)
        self.assertIn(line_of("        return b + 1"), lines)
        self.assertIn(line_of("    return inner(a) + VALUE"), lines)
        self.assertIn(line_of("        return 2"), lines)

    def test_nested_function_lines_are_present(self):
        lines = executable_lines(SOURCE, "m.py")
        self.assertIn(line_of("        return b + 1"), lines)
        self.assertIn(line_of("    return inner(a) + VALUE"), lines)

    def test_function_for_line_innermost(self):
        spans = function_spans(SOURCE, "m.py")
        inner = function_for_line(spans, line_of("        return b + 1"))
        self.assertIsNotNone(inner)
        self.assertEqual(inner.co_name, "inner")
        outer = function_for_line(spans, line_of("    return inner(a) + VALUE"))
        self.assertIsNotNone(outer)
        self.assertEqual(outer.co_name, "outer")

    def test_qualified_names(self):
        spans = function_spans(SOURCE, "m.py")
        names = {span.name for span in spans}
        self.assertIn("outer", names)
        self.assertIn("outer.inner", names)
        self.assertIn("Thing.method", names)
        self.assertIn("<lambda>", names)

    def test_syntax_error_returns_none_and_no_spans(self):
        bad = "def f(:\n    pass\n"
        self.assertIsNone(executable_lines(bad, "bad.py"))
        self.assertEqual(function_spans(bad, "bad.py"), [])

    def test_line_outside_any_function(self):
        spans = function_spans(SOURCE, "m.py")
        self.assertIsNone(function_for_line(spans, line_of("VALUE = 1")))


if __name__ == "__main__":
    unittest.main()
