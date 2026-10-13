"""UndoStack と undo_operation のテスト。"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# ── UndoStack ─────────────────────────────────────────────────────────────────


class UndoStackTests(unittest.TestCase):
    def _stack(self):
        from ore_filer.services.undo import UndoRecord, UndoStack
        s = UndoStack()
        return s, UndoRecord

    def test_empty_peek_returns_none(self) -> None:
        s, _ = self._stack()
        self.assertIsNone(s.peek())

    def test_empty_pop_returns_none(self) -> None:
        s, _ = self._stack()
        self.assertIsNone(s.pop())

    def test_push_peek(self) -> None:
        s, R = self._stack()
        rec = R(kind="create", label="test", entries=[])
        s.push(rec)
        self.assertIs(s.peek(), rec)

    def test_push_pop_lifo(self) -> None:
        s, R = self._stack()
        r1 = R(kind="copy", label="r1", entries=[])
        r2 = R(kind="move", label="r2", entries=[])
        s.push(r1)
        s.push(r2)
        self.assertIs(s.pop(), r2)
        self.assertIs(s.pop(), r1)
        self.assertIsNone(s.pop())

    def test_max_size_evicts_oldest(self) -> None:
        from ore_filer.services.undo import UndoRecord, UndoStack
        s = UndoStack()
        records = [UndoRecord(kind="copy", label=str(i), entries=[]) for i in range(25)]
        for r in records:
            s.push(r)
        self.assertEqual(len(s), UndoStack.MAX_SIZE)
        # 先頭は古い方が捨てられ、末尾（最新）が残る
        self.assertIs(s.peek(), records[-1])

    def test_len(self) -> None:
        s, R = self._stack()
        self.assertEqual(len(s), 0)
        s.push(R(kind="rename", label="a", entries=[]))
        self.assertEqual(len(s), 1)


# ── undo_operation: move/rename kind ────────────────────────────────────────────


class UndoOperationMoveTests(unittest.TestCase):
    def test_move_undo_restores_file(self) -> None:
        from ore_filer.services.undo import UndoEntry, UndoRecord, undo_operation
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "orig.txt"
            moved = root / "moved.txt"
            original.write_text("data", encoding="utf-8")
            original.rename(moved)
            rec = UndoRecord(
                kind="move",
                label="テスト移動",
                entries=[UndoEntry(current=moved, original=original)],
            )
            skipped = undo_operation(rec)
            self.assertEqual(skipped, [])
            self.assertTrue(original.exists())
            self.assertFalse(moved.exists())

    def test_move_undo_skips_missing_current(self) -> None:
        from ore_filer.services.undo import UndoEntry, UndoRecord, undo_operation
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rec = UndoRecord(
                kind="move",
                label="test",
                entries=[UndoEntry(current=root / "no_such.txt", original=root / "orig.txt")],
            )
            skipped = undo_operation(rec)
            self.assertEqual(len(skipped), 1)
            self.assertIn("no_such.txt", skipped[0])

    def test_move_undo_skips_occupied_original(self) -> None:
        from ore_filer.services.undo import UndoEntry, UndoRecord, undo_operation
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            moved = root / "moved.txt"
            original = root / "orig.txt"
            moved.write_text("m", encoding="utf-8")
            original.write_text("already", encoding="utf-8")
            rec = UndoRecord(
                kind="move",
                label="test",
                entries=[UndoEntry(current=moved, original=original)],
            )
            skipped = undo_operation(rec)
            self.assertEqual(len(skipped), 1)
            self.assertIn("orig.txt", skipped[0])
            self.assertTrue(moved.exists())

    def test_rename_undo_restores_name(self) -> None:
        from ore_filer.services.undo import UndoEntry, UndoRecord, undo_operation
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_path = root / "old_name.txt"
            new_path = root / "new_name.txt"
            old_path.write_bytes(b"x")
            old_path.rename(new_path)
            rec = UndoRecord(
                kind="rename",
                label="テスト",
                entries=[UndoEntry(current=new_path, original=old_path)],
            )
            skipped = undo_operation(rec)
            self.assertEqual(skipped, [])
            self.assertTrue(old_path.exists())
            self.assertFalse(new_path.exists())

    def test_progress_callback_called(self) -> None:
        from ore_filer.services.undo import UndoEntry, UndoRecord, undo_operation
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = []
            for i in range(3):
                cur = root / f"c{i}.txt"
                orig = root / f"o{i}.txt"
                cur.write_bytes(b"x")
                files.append(UndoEntry(current=cur, original=orig))
            calls: list[tuple[int, int]] = []
            rec = UndoRecord(kind="move", label="test", entries=files)
            undo_operation(rec, progress_cb=lambda d, t: calls.append((d, t)))
            self.assertEqual(len(calls), 3)
            self.assertEqual(calls[-1], (3, 3))


# ── undo_operation: create kind（QFile.moveToTrash 使用）───────────────────────


class UndoOperationCreateTests(unittest.TestCase):
    def test_create_undo_trashes_file(self) -> None:
        from ore_filer.services.undo import UndoEntry, UndoRecord, undo_operation
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = root / "new_folder"
            created.mkdir()
            rec = UndoRecord(
                kind="create",
                label="フォルダを作成: new_folder",
                entries=[UndoEntry(current=created, original=None)],
            )
            skipped = undo_operation(rec)
            # ごみ箱移動後はファイルが元の場所に存在しない
            self.assertEqual(skipped, [])
            self.assertFalse(created.exists())

    def test_create_undo_skips_missing(self) -> None:
        from ore_filer.services.undo import UndoEntry, UndoRecord, undo_operation
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rec = UndoRecord(
                kind="create",
                label="test",
                entries=[UndoEntry(current=root / "ghost.txt", original=None)],
            )
            skipped = undo_operation(rec)
            self.assertEqual(len(skipped), 1)


# ── キーバインディングに undo が登録されていること ───────────────────────────────


class UndoKeymapTests(unittest.TestCase):
    def test_undo_in_default_bindings(self) -> None:
        from ore_filer.gui.keymap import DEFAULT_BINDINGS
        self.assertIn("undo", DEFAULT_BINDINGS)
        self.assertEqual(DEFAULT_BINDINGS["undo"], "Ctrl+Z")

    def test_undo_in_action_descriptions(self) -> None:
        from ore_filer.gui.keymap import ACTION_DESCRIPTIONS
        self.assertIn("undo", ACTION_DESCRIPTIONS)

    def test_no_key_conflict_with_undo(self) -> None:
        from ore_filer.gui.keymap import DEFAULT_BINDINGS, _parse_spec
        seen: dict = {}
        for action, spec in DEFAULT_BINDINGS.items():
            parsed = _parse_spec(spec)
            self.assertNotIn(
                parsed, seen,
                f"キー重複: {spec!r} が {seen.get(parsed)!r} と {action!r} の両方に割り当て",
            )
            seen[parsed] = action

    def test_run_action_undo_calls_undo_last(self) -> None:
        from unittest import mock
        from ore_filer.gui.main_window import MainWindow
        with TemporaryDirectory() as tmp:
            mw = MainWindow(str(tmp), str(tmp))
            _app.processEvents()
            with mock.patch.object(mw, "undo_last") as mock_undo:
                result = mw._run_action("undo")
            mw.close()
        self.assertTrue(result)
        mock_undo.assert_called_once()


if __name__ == "__main__":
    unittest.main()
