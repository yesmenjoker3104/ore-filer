from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from ore_filer.gui.dialogs.file_list_dialogs import LargeFolderDialog
from ore_filer.gui.keymap import KeyMap
from ore_filer.gui.main_window_archive import MainWindowArchiveMixin

_app = QApplication.instance() or QApplication([])


class CommandPaletteRegistryTests(unittest.TestCase):
    def test_unbound_actions_are_available(self) -> None:
        actions = dict((action, shortcut) for action, shortcut, _ in KeyMap().palette_bindings())

        for action in (
            "large_folders",
            "new_folder",
            "git_stage",
            "git_commit",
            "git_pull",
            "git_switch_branch",
            "view_text",
        ):
            self.assertIn(action, actions)

        self.assertEqual(actions["git_commit"], "")
        self.assertEqual(actions["large_folders"], "Ctrl+Shift+I")


class LargeFolderDialogTests(unittest.TestCase):
    def test_lists_direct_child_folders_largest_first(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            small = root / "small"
            large = root / "large"
            small.mkdir()
            large.mkdir()
            (small / "file.bin").write_bytes(b"x")
            (large / "file.bin").write_bytes(b"x" * 2048)

            dialog = LargeFolderDialog(root)
            deadline = time.monotonic() + 10
            while dialog._thread is not None and time.monotonic() < deadline:
                _app.processEvents()
                time.sleep(0.01)

            self.assertIsNone(dialog._thread, "フォルダサイズ計算がタイムアウトしました")
            self.assertEqual(dialog.result_table.rowCount(), 2)
            self.assertEqual(dialog.result_table.item(0, 0).text(), "large")
            self.assertEqual(dialog.result_table.item(1, 0).text(), "small")
            dialog.close()


class ArchiveConfirmationTests(unittest.TestCase):
    def test_extract_defaults_to_yes(self) -> None:
        pane = mock.Mock()
        pane.selected_paths.return_value = [Path("archive.zip")]
        pane.current_path = Path(".")
        window = mock.Mock()
        window._archive_thread = None
        window.active_pane = pane
        window.inactive_pane = pane
        with mock.patch(
            "ore_filer.gui.main_window_archive.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            MainWindowArchiveMixin.extract_archives(window)

        self.assertEqual(question.call_args.args[4], QMessageBox.StandardButton.Yes)
