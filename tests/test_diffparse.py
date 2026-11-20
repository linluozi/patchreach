"""Unit tests for the unified-diff parser."""

import unittest

from patchreach.diffparse import DiffParseError, parse_unified_diff


class ParseTests(unittest.TestCase):
    def test_simple_addition(self):
        text = (
            "--- a/pkg/impl.py\n"
            "+++ b/pkg/impl.py\n"
            "@@ -1,2 +1,3 @@\n"
            " def f():\n"
            "+    return 1\n"
            "     pass\n"
        )
        diff = parse_unified_diff(text)
        self.assertEqual(diff.paths(), ["pkg/impl.py"])
        self.assertEqual(diff.files["pkg/impl.py"].added, [2])

    def test_replacement_marks_new_line(self):
        text = (
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -3,1 +3,1 @@\n"
            "-old = 1\n"
            "+new = 2\n"
        )
        diff = parse_unified_diff(text)
        self.assertEqual(diff.files["x.py"].added, [3])
        self.assertEqual(diff.files["x.py"].removed, 1)

    def test_multiple_hunks_and_files(self):
        text = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,2 +1,3 @@\n"
            " x\n"
            "+y\n"
            " z\n"
            "@@ -10,1 +11,2 @@\n"
            " q\n"
            "+r\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        diff = parse_unified_diff(text)
        self.assertEqual(diff.paths(), ["a.py", "b.py"])
        self.assertEqual(diff.files["a.py"].added, [2, 12])
        self.assertEqual(diff.files["a.py"].hunks, 2)
        self.assertEqual(diff.files["b.py"].added, [1])

    def test_new_file(self):
        text = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+a = 1\n"
            "+b = 2\n"
        )
        diff = parse_unified_diff(text)
        changed = diff.files["new.py"]
        self.assertTrue(changed.is_new)
        self.assertEqual(changed.added, [1, 2])

    def test_deleted_file(self):
        text = (
            "diff --git a/gone.py b/gone.py\n"
            "deleted file mode 100644\n"
            "--- a/gone.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-a = 1\n"
            "-b = 2\n"
        )
        diff = parse_unified_diff(text)
        self.assertIn("gone.py", diff.files)
        changed = diff.files["gone.py"]
        self.assertTrue(changed.is_deleted)
        self.assertEqual(changed.added, [])
        self.assertEqual(changed.removed, 2)

    def test_rename_uses_new_path(self):
        text = (
            "diff --git a/old.py b/new.py\n"
            "similarity index 90%\n"
            "rename from old.py\n"
            "rename to new.py\n"
            "--- a/old.py\n"
            "+++ b/new.py\n"
            "@@ -1 +1,2 @@\n"
            " x\n"
            "+y\n"
        )
        diff = parse_unified_diff(text)
        self.assertEqual(diff.paths(), ["new.py"])
        self.assertEqual(diff.files["new.py"].added, [2])

    def test_no_newline_marker(self):
        text = (
            "--- a/x.py\n"
            "+++ b/x.py\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
            "\\ No newline at end of file\n"
        )
        diff = parse_unified_diff(text)
        self.assertEqual(diff.files["x.py"].added, [1])
        self.assertEqual(diff.files["x.py"].removed, 1)

    def test_quoted_path_with_spaces(self):
        text = (
            '--- "a/has space.py"\n'
            '+++ "b/has space.py"\n'
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        diff = parse_unified_diff(text)
        self.assertEqual(diff.paths(), ["has space.py"])

    def test_malformed_hunk_raises(self):
        text = "--- a/x.py\n+++ b/x.py\n@@ nonsense @@\n"
        with self.assertRaises(DiffParseError):
            parse_unified_diff(text)

    def test_empty_diff(self):
        diff = parse_unified_diff("")
        self.assertEqual(diff.paths(), [])
        self.assertEqual(diff.added_lines, 0)

    def test_mode_only_diff_has_no_files(self):
        text = (
            "diff --git a/x.py b/x.py\n"
            "old mode 100644\n"
            "new mode 100755\n"
        )
        diff = parse_unified_diff(text)
        self.assertEqual(diff.paths(), [])

    def test_hunk_without_counts(self):
        text = "--- a/x.py\n+++ b/x.py\n@@ -5 +5 @@\n-a\n+b\n"
        diff = parse_unified_diff(text)
        self.assertEqual(diff.files["x.py"].added, [5])

    def test_crlf(self):
        text = (
            "--- a/x.py\r\n"
            "+++ b/x.py\r\n"
            "@@ -1 +1,2 @@\r\n"
            " a\r\n"
            "+b\r\n"
        )
        diff = parse_unified_diff(text)
        self.assertEqual(diff.files["x.py"].added, [2])


if __name__ == "__main__":
    unittest.main()
