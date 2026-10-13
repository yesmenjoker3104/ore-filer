"""PreviewPanel と プレビューモードのテスト。"""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# ── PreviewPanel.set_path ─────────────────────────────────────────────────────


class PreviewPanelTests(unittest.TestCase):
    def _panel(self):
        from ore_filer.gui.preview_panel import PreviewPanel
        p = PreviewPanel()
        p.show()
        _app.processEvents()
        return p

    def test_set_path_none_does_not_raise(self) -> None:
        panel = self._panel()
        panel.set_path(None)
        _app.processEvents()
        panel.close()

    def test_set_path_text_file(self) -> None:
        panel = self._panel()
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "hello.txt"
            f.write_text("line1\nline2\n", encoding="utf-8")
            panel.set_path(f)
            _app.processEvents()
        panel.close()

    def test_set_path_folder(self) -> None:
        panel = self._panel()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"")
            panel.set_path(root)
            _app.processEvents()
        panel.close()

    def test_set_path_unknown_extension(self) -> None:
        panel = self._panel()
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "data.xyz123"
            f.write_bytes(b"\x01\x02\x03")
            panel.set_path(f)
            _app.processEvents()
        panel.close()

    def test_set_path_missing_file_does_not_raise(self) -> None:
        panel = self._panel()
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no_such_file.txt"
            panel.set_path(missing)
            _app.processEvents()
        panel.close()

    def test_set_path_image_file(self) -> None:
        """PNG ファイル（最小 1x1）を set_path しても例外が出ないこと。"""
        import struct, zlib
        panel = self._panel()
        with TemporaryDirectory() as tmp:
            # 最小 PNG (1x1 px, RGBA)
            def _make_png():
                sig = b"\x89PNG\r\n\x1a\n"
                def chunk(name, data):
                    c = struct.pack(">I", len(data)) + name + data
                    return c + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
                ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
                idat_data = zlib.compress(b"\x00\xff\x00\x00")
                return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat_data) + chunk(b"IEND", b"")
            img = Path(tmp) / "test.png"
            img.write_bytes(_make_png())
            panel.set_path(img)
            _app.processEvents()
        panel.close()


# ── キーバインディング ─────────────────────────────────────────────────────────


class PreviewKeymapTests(unittest.TestCase):
    def test_toggle_preview_in_default_bindings(self) -> None:
        from ore_filer.gui.keymap import DEFAULT_BINDINGS
        self.assertIn("toggle_preview", DEFAULT_BINDINGS)
        self.assertEqual(DEFAULT_BINDINGS["toggle_preview"], "Ctrl+Q")

    def test_toggle_preview_in_action_descriptions(self) -> None:
        from ore_filer.gui.keymap import ACTION_DESCRIPTIONS
        self.assertIn("toggle_preview", ACTION_DESCRIPTIONS)

    def test_no_key_conflict(self) -> None:
        from ore_filer.gui.keymap import DEFAULT_BINDINGS, _parse_spec
        seen: dict = {}
        for action, spec in DEFAULT_BINDINGS.items():
            parsed = _parse_spec(spec)
            self.assertNotIn(
                parsed, seen,
                f"キー重複: {spec!r} が {seen.get(parsed)!r} と {action!r} の両方に割り当て",
            )
            seen[parsed] = action

    def test_run_action_toggle_preview(self) -> None:
        from unittest import mock
        from ore_filer.gui.main_window import MainWindow
        with TemporaryDirectory() as tmp:
            mw = MainWindow(str(tmp), str(tmp))
            _app.processEvents()
            with mock.patch.object(mw, "toggle_preview") as m:
                result = mw._run_action("toggle_preview")
            mw.close()
        self.assertTrue(result)
        m.assert_called_once()


# ── MainWindow プレビュー初期化 ───────────────────────────────────────────────


class MainWindowPreviewInitTests(unittest.TestCase):
    def test_preview_attributes_initialized(self) -> None:
        from ore_filer.gui.main_window import MainWindow
        with TemporaryDirectory() as tmp:
            mw = MainWindow(str(tmp), str(tmp))
            _app.processEvents()
            self.assertFalse(mw._preview_enabled)
            self.assertIsNotNone(mw._left_stack)
            self.assertIsNotNone(mw._right_stack)
            self.assertIsNotNone(mw._left_preview)
            self.assertIsNotNone(mw._right_preview)
            mw.close()

    def test_toggle_preview_changes_state(self) -> None:
        from ore_filer.gui.main_window import MainWindow
        with TemporaryDirectory() as tmp:
            mw = MainWindow(str(tmp), str(tmp))
            _app.processEvents()
            self.assertFalse(mw._preview_enabled)
            mw.toggle_preview()
            _app.processEvents()
            self.assertTrue(mw._preview_enabled)
            mw.toggle_preview()
            _app.processEvents()
            self.assertFalse(mw._preview_enabled)
            mw.close()


if __name__ == "__main__":
    unittest.main()
