import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ore_filer.services.file_operations import read_text_pair, text_diff


class ReadTextPairTests(unittest.TestCase):
    def test_returns_both_file_contents(self) -> None:
        with TemporaryDirectory() as tmp:
            left = Path(tmp) / "left.txt"
            right = Path(tmp) / "right.txt"
            left.write_bytes(b"hello\n")
            right.write_bytes(b"world\n")

            l_text, r_text = read_text_pair(left, right)
            self.assertEqual(l_text, "hello\n")
            self.assertEqual(r_text, "world\n")

    def test_raises_value_error_for_missing_file(self) -> None:
        with TemporaryDirectory() as tmp:
            left = Path(tmp) / "left.txt"
            right = Path(tmp) / "missing.txt"
            left.write_text("hello\n", encoding="utf-8")

            with self.assertRaises((ValueError, OSError)):
                read_text_pair(left, right)

    def test_same_content_returns_equal_strings(self) -> None:
        with TemporaryDirectory() as tmp:
            left = Path(tmp) / "a.txt"
            right = Path(tmp) / "b.txt"
            left.write_text("same\n", encoding="utf-8")
            right.write_text("same\n", encoding="utf-8")

            l_text, r_text = read_text_pair(left, right)
            self.assertEqual(l_text, r_text)


class TextDiffTests(unittest.TestCase):
    def test_detects_added_line(self) -> None:
        with TemporaryDirectory() as tmp:
            left = Path(tmp) / "left.txt"
            right = Path(tmp) / "right.txt"
            left.write_text("line1\n", encoding="utf-8")
            right.write_text("line1\nline2\n", encoding="utf-8")

            diff = text_diff(left, right)
            self.assertIn("+line2", diff)

    def test_detects_removed_line(self) -> None:
        with TemporaryDirectory() as tmp:
            left = Path(tmp) / "left.txt"
            right = Path(tmp) / "right.txt"
            left.write_text("line1\nline2\n", encoding="utf-8")
            right.write_text("line1\n", encoding="utf-8")

            diff = text_diff(left, right)
            self.assertIn("-line2", diff)

    def test_empty_diff_for_identical_files(self) -> None:
        with TemporaryDirectory() as tmp:
            left = Path(tmp) / "left.txt"
            right = Path(tmp) / "right.txt"
            left.write_text("same\n", encoding="utf-8")
            right.write_text("same\n", encoding="utf-8")

            self.assertEqual(text_diff(left, right), "")


if __name__ == "__main__":
    unittest.main()
