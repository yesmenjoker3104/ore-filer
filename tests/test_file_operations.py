"""copy_items / move_items / unique_path と _resolve_conflicts のテスト。"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

_app = QApplication.instance() or QApplication([])


# ── unique_path ───────────────────────────────────────────────────────────────


class UniquPathTests(unittest.TestCase):
    def test_no_conflict_returns_same(self) -> None:
        from ore_filer.services.file_operations import unique_path
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "new.txt"
            self.assertEqual(unique_path(target), target)

    def test_conflict_returns_numbered(self) -> None:
        from ore_filer.services.file_operations import unique_path
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "file.txt"
            target.write_bytes(b"")
            result = unique_path(target)
        self.assertEqual(result.name, "file (2).txt")

    def test_multiple_conflicts_increment(self) -> None:
        from ore_filer.services.file_operations import unique_path
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "file.txt").write_bytes(b"")
            (root / "file (2).txt").write_bytes(b"")
            result = unique_path(root / "file.txt")
        self.assertEqual(result.name, "file (3).txt")

    def test_directory_unique_path(self) -> None:
        from ore_filer.services.file_operations import unique_path
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "mydir").mkdir()
            result = unique_path(root / "mydir")
        self.assertEqual(result.name, "mydir (2)")


# ── copy_items ────────────────────────────────────────────────────────────────


class CopyItemsTests(unittest.TestCase):
    def test_copies_file(self) -> None:
        from ore_filer.services.file_operations import copy_items
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.txt"
            dst = root / "dst.txt"
            src.write_text("hello", encoding="utf-8")
            copy_items([(src, dst)])
            self.assertEqual(dst.read_text(encoding="utf-8"), "hello")

    def test_copies_directory(self) -> None:
        from ore_filer.services.file_operations import copy_items
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src_dir"
            src.mkdir()
            (src / "child.txt").write_bytes(b"data")
            dst = root / "dst_dir"
            copy_items([(src, dst)])
            self.assertTrue((dst / "child.txt").exists())

    def test_overwrites_existing(self) -> None:
        from ore_filer.services.file_operations import copy_items
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.txt"
            dst = root / "dst.txt"
            src.write_text("new", encoding="utf-8")
            dst.write_text("old", encoding="utf-8")
            copy_items([(src, dst)])
            self.assertEqual(dst.read_text(encoding="utf-8"), "new")

    def test_creates_parent_dirs(self) -> None:
        from ore_filer.services.file_operations import copy_items
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.txt"
            dst = root / "sub" / "deep" / "dst.txt"
            src.write_bytes(b"x")
            copy_items([(src, dst)])
            self.assertTrue(dst.exists())

    def test_progress_callback(self) -> None:
        from ore_filer.services.file_operations import copy_items
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls: list[tuple] = []
            files = []
            for i in range(3):
                src = root / f"s{i}.txt"
                src.write_bytes(b"x")
                files.append((src, root / f"d{i}.txt"))
            copy_items(files, progress_cb=lambda d, t: calls.append((d, t)))
        self.assertEqual(calls, [(1, 3), (2, 3), (3, 3)])


# ── move_items ────────────────────────────────────────────────────────────────


class MoveItemsTests(unittest.TestCase):
    def test_moves_file(self) -> None:
        from ore_filer.services.file_operations import move_items
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.txt"
            dst = root / "dst.txt"
            src.write_text("data", encoding="utf-8")
            move_items([(src, dst)])
            self.assertFalse(src.exists())
            self.assertEqual(dst.read_text(encoding="utf-8"), "data")

    def test_overwrites_existing(self) -> None:
        from ore_filer.services.file_operations import move_items
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.txt"
            dst = root / "dst.txt"
            src.write_text("new", encoding="utf-8")
            dst.write_text("old", encoding="utf-8")
            move_items([(src, dst)])
            self.assertEqual(dst.read_text(encoding="utf-8"), "new")


# ── _resolve_conflicts ────────────────────────────────────────────────────────


class ResolveConflictsTests(unittest.TestCase):
    """MainWindow._resolve_conflicts の動作をモックで検証する。"""

    def _make_mw(self, tmp: Path):
        from ore_filer.gui.main_window import MainWindow
        mw = MainWindow(str(tmp), str(tmp))
        _app.processEvents()
        return mw

    def test_no_conflict_returns_all_pairs(self) -> None:
        """衝突なしのペアはそのまま返る。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.txt"
            src.write_bytes(b"")
            dst = root / "dest" / "a.txt"
            dst.parent.mkdir()
            mw = self._make_mw(root)
            result = mw._resolve_conflicts([(src, dst)])
            mw.close()
        self.assertEqual(result, [(src, dst)])

    def test_overwrite_action_keeps_pair(self) -> None:
        """「上書き」を選んだ場合、元の dst でペアが返る。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.txt"
            dst = root / "b.txt"
            src.write_bytes(b"new")
            dst.write_bytes(b"old")
            mw = self._make_mw(root)
            with mock.patch(
                "ore_filer.gui.main_window_file_ops.ConflictDialog"
            ) as MockDlg:
                inst = MockDlg.return_value
                inst.exec.return_value = QDialog.DialogCode.Accepted
                inst.chosen_action.return_value = "overwrite"
                inst.apply_to_all.return_value = False
                result = mw._resolve_conflicts([(src, dst)])
            mw.close()
        self.assertEqual(result, [(src, dst)])

    def test_skip_action_removes_pair(self) -> None:
        """「スキップ」を選んだ場合、そのペアは除外される。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.txt"
            dst = root / "b.txt"
            src.write_bytes(b"x")
            dst.write_bytes(b"y")
            mw = self._make_mw(root)
            with mock.patch(
                "ore_filer.gui.main_window_file_ops.ConflictDialog"
            ) as MockDlg:
                inst = MockDlg.return_value
                inst.exec.return_value = QDialog.DialogCode.Accepted
                inst.chosen_action.return_value = "skip"
                inst.apply_to_all.return_value = False
                result = mw._resolve_conflicts([(src, dst)])
            mw.close()
        self.assertEqual(result, [])

    def test_rename_action_returns_unique_dst(self) -> None:
        """「別名で保存」を選んだ場合、衝突しない名前の dst が返る。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.txt"
            dst = root / "b.txt"
            src.write_bytes(b"x")
            dst.write_bytes(b"y")
            mw = self._make_mw(root)
            with mock.patch(
                "ore_filer.gui.main_window_file_ops.ConflictDialog"
            ) as MockDlg:
                inst = MockDlg.return_value
                inst.exec.return_value = QDialog.DialogCode.Accepted
                inst.chosen_action.return_value = "rename"
                inst.apply_to_all.return_value = False
                result = mw._resolve_conflicts([(src, dst)])
            mw.close()
        self.assertEqual(len(result), 1)
        self.assertNotEqual(result[0][1], dst)
        self.assertFalse(result[0][1].exists())

    def test_cancel_returns_none(self) -> None:
        """キャンセル時は None が返り操作全体が中止される。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "a.txt"
            dst = root / "b.txt"
            src.write_bytes(b"x")
            dst.write_bytes(b"y")
            mw = self._make_mw(root)
            with mock.patch(
                "ore_filer.gui.main_window_file_ops.ConflictDialog"
            ) as MockDlg:
                inst = MockDlg.return_value
                inst.exec.return_value = QDialog.DialogCode.Rejected
                result = mw._resolve_conflicts([(src, dst)])
            mw.close()
        self.assertIsNone(result)

    def test_apply_to_all_skips_subsequent_dialogs(self) -> None:
        """「以降すべてに適用」がチェック済みなら 2 件目以降はダイアログが出ない。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pairs = []
            for i in range(3):
                src = root / f"s{i}.txt"
                dst = root / f"d{i}.txt"
                src.write_bytes(b"x")
                dst.write_bytes(b"y")
                pairs.append((src, dst))
            mw = self._make_mw(root)
            with mock.patch(
                "ore_filer.gui.main_window_file_ops.ConflictDialog"
            ) as MockDlg:
                inst = MockDlg.return_value
                inst.exec.return_value = QDialog.DialogCode.Accepted
                inst.chosen_action.return_value = "skip"
                inst.apply_to_all.return_value = True
                result = mw._resolve_conflicts(pairs)
                call_count = MockDlg.call_count
            mw.close()
        # ダイアログは 1 回だけ開く
        self.assertEqual(call_count, 1)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
