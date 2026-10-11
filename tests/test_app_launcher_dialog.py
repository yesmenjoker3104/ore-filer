from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from ore_filer.gui.app_launcher_dialog import AppLauncherDialog
from ore_filer.gui.keymap import DEFAULT_BINDINGS
from ore_filer.services.app_launcher import (
    find_app_launcher,
    list_registered_apps,
    load_registry,
    register_application,
    scan_application_directory,
)

EXE = find_app_launcher()
NOTEPAD = Path(r"C:\Windows\System32\notepad.exe")
CALC = Path(r"C:\Windows\System32\calc.exe")


@unittest.skipIf(EXE is None, "app-launcher.exe が見つかりません")
class AppLauncherDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        # 登録データを一時フォルダへ隔離する（CLIは環境変数 LOCALAPPDATA を見る）
        self._tmp = tempfile.mkdtemp(prefix="ore_filer_al_")
        self._old_local_app_data = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = self._tmp
        # 想定外のエラーでモーダルが開いてテストが止まらないようにする
        for name in ("warning", "information"):
            patcher = patch(f"ore_filer.gui.app_launcher_dialog.QMessageBox.{name}")
            self.addCleanup(patcher.stop)
            patcher.start()

    def tearDown(self) -> None:
        if self._old_local_app_data is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = self._old_local_app_data
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _names(self) -> list[str]:
        return [app.name for app in list_registered_apps(EXE)]

    def _select_source(self, dialog: AppLauncherDialog, kind: str, path: str) -> None:
        for row in range(dialog._source_list.count()):
            item = dialog._source_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == (kind, path):
                dialog._source_list.setCurrentRow(row)
                return
        self.fail(f"登録元に {kind}:{path} がありません")

    def _source_path(self, kind: str, path_text: str) -> str:
        if kind == "folder":
            return next(s.path for s in load_registry(EXE)[1] if s.path == str(Path(path_text)))
        return next(a.path for a in load_registry(EXE)[0] if a.path == str(Path(path_text)))

    def _make_folder_with_exe(self, name: str, *exe_names: str) -> Path:
        folder = Path(self._tmp) / name
        folder.mkdir(parents=True, exist_ok=True)
        for exe_name in exe_names:
            (folder / exe_name).write_bytes(b"MZ")
        return folder

    def test_lists_registered_apps(self) -> None:
        register_application(EXE, NOTEPAD, "メモ帳")
        dialog = AppLauncherDialog(EXE, None)
        listed = [dialog._list.item(i).data(Qt.ItemDataRole.UserRole).name for i in range(dialog._list.count())]
        self.assertIn("メモ帳", listed)

    def test_right_key_switches_to_settings_tab(self) -> None:
        register_application(EXE, NOTEPAD, "メモ帳")
        dialog = AppLauncherDialog(EXE, None)
        dialog._list.setFocus()
        QTest.keyClick(dialog._list, Qt.Key.Key_Right)
        self.assertEqual(dialog._tabs.currentIndex(), 1)

    def test_list_has_focus_when_shown_and_down_moves_cursor(self) -> None:
        register_application(EXE, NOTEPAD, "メモ帳")
        register_application(EXE, CALC, "電卓")
        dialog = AppLauncherDialog(EXE, None)
        dialog.show()
        self.qt_app.processEvents()
        self.assertTrue(dialog._list.hasFocus())
        self.assertEqual(dialog._list.currentRow(), 0)
        QTest.keyClick(dialog._list, Qt.Key.Key_Down)
        self.assertEqual(dialog._list.currentRow(), 1)
        dialog.close()

    def test_typing_moves_to_matching_app_case_insensitively(self) -> None:
        register_application(EXE, NOTEPAD, "Memo Pad")
        register_application(EXE, CALC, "Calculator")
        dialog = AppLauncherDialog(EXE, None)
        dialog._list.setFocus()
        QTest.keyClicks(dialog._list, "CALC")
        self.assertEqual(dialog._list.currentItem().data(Qt.ItemDataRole.UserRole).name, "Calculator")
        self.assertEqual(dialog._hint_label.text(), "フィルタ: CALC")

    def test_backspace_shrinks_search_and_escape_clears_it(self) -> None:
        register_application(EXE, NOTEPAD, "Memo Pad")
        register_application(EXE, CALC, "Calculator")
        dialog = AppLauncherDialog(EXE, None)
        dialog._list.setFocus()
        QTest.keyClicks(dialog._list, "calc")
        QTest.keyClick(dialog._list, Qt.Key.Key_Backspace)
        self.assertEqual(dialog._hint_label.text(), "フィルタ: cal")
        QTest.keyClick(dialog._list, Qt.Key.Key_Escape)
        self.assertEqual(dialog._list._search, "")
        self.assertEqual(dialog._hint_label.text(), "Enter: 起動 / →: アプリ設定 / 文字入力: インクリメンタルフィルタ")
        dialog.close()

    def test_search_resets_after_idle_timeout(self) -> None:
        register_application(EXE, NOTEPAD, "Memo Pad")
        dialog = AppLauncherDialog(EXE, None)
        dialog._list.setFocus()
        QTest.keyClicks(dialog._list, "memo")
        dialog._list._reset_timer.timeout.emit()
        self.assertEqual(dialog._list._search, "")
        self.assertEqual(dialog._hint_label.text(), "Enter: 起動 / →: アプリ設定 / 文字入力: インクリメンタルフィルタ")

    def test_settings_lists_only_folder_sources(self) -> None:
        folder = Path(self._tmp) / "apps"
        folder.mkdir()
        (folder / "top.exe").write_bytes(b"MZ")
        scan_application_directory(EXE, folder, recursive=False)
        register_application(EXE, NOTEPAD, "メモ帳")
        dialog = AppLauncherDialog(EXE, None)
        keys = [dialog._source_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dialog._source_list.count())]
        self.assertEqual(keys, [("folder", self._source_path("folder", str(folder)))])
        self.assertFalse(hasattr(dialog, "_add_exe"))

    def test_selecting_folder_shows_detected_exes(self) -> None:
        folder = Path(self._tmp) / "apps"
        (folder / "sub").mkdir(parents=True)
        (folder / "top.exe").write_bytes(b"MZ")
        (folder / "sub" / "deep.exe").write_bytes(b"MZ")
        scan_application_directory(EXE, folder, recursive=False)
        dialog = AppLauncherDialog(EXE, None)
        self._select_source(dialog, "folder", self._source_path("folder", str(folder)))
        names = [dialog._app_table.item(r, 0).text() for r in range(dialog._app_table.rowCount())]
        self.assertEqual(names, ["top"])
        self.assertTrue(dialog._rescan_button.isEnabled())
        self.assertTrue(dialog._remove_button.isEnabled())

    def test_editing_alias_saves_aliases(self) -> None:
        folder = self._make_folder_with_exe("apps", "tool.exe")
        scan_application_directory(EXE, folder, recursive=False)
        dialog = AppLauncherDialog(EXE, None)
        self._select_source(dialog, "folder", self._source_path("folder", str(folder)))
        dialog._app_table.item(0, 1).setText("tl, t")
        self.qt_app.processEvents()
        app = next(a for a in load_registry(EXE)[0] if a.name == "tool")
        self.assertEqual(app.aliases, ("tl", "t"))

    def test_unchecking_visibility_hides_app_from_list(self) -> None:
        folder = self._make_folder_with_exe("apps", "tool.exe", "other.exe")
        scan_application_directory(EXE, folder, recursive=False)
        dialog = AppLauncherDialog(EXE, None)
        self._select_source(dialog, "folder", self._source_path("folder", str(folder)))
        row = next(r for r in range(dialog._app_table.rowCount()) if dialog._app_table.item(r, 0).text() == "tool")
        dialog._app_table.item(row, 2).setCheckState(Qt.CheckState.Unchecked)
        self.qt_app.processEvents()
        hidden = next(a for a in load_registry(EXE)[0] if a.name == "tool")
        self.assertFalse(hidden.show_in_list)
        listed = [dialog._list.item(i).data(Qt.ItemDataRole.UserRole).name for i in range(dialog._list.count())]
        self.assertNotIn("tool", listed)
        self.assertIn("other", listed)

    def test_recursive_checkbox_rescans_when_toggled(self) -> None:
        folder = self._make_folder_with_exe("apps", "top.exe")
        (folder / "sub").mkdir()
        (folder / "sub" / "deep.exe").write_bytes(b"MZ")
        scan_application_directory(EXE, folder, recursive=False)
        dialog = AppLauncherDialog(EXE, None)
        self._select_source(dialog, "folder", self._source_path("folder", str(folder)))
        self.assertFalse(dialog._recursive_check.isChecked())

        dialog._recursive_check.click()
        self.assertTrue(load_registry(EXE)[1][0].recursive)
        self.assertIn("deep", self._names())

        dialog._recursive_check.click()
        self.assertFalse(load_registry(EXE)[1][0].recursive)
        self.assertNotIn("deep", self._names())

    def test_adding_parent_folder_absorbs_child_source_and_keeps_alias(self) -> None:
        parent = Path(self._tmp) / "apps"
        child = parent / "sub"
        child.mkdir(parents=True)
        (child / "deep.exe").write_bytes(b"MZ")
        scan_application_directory(EXE, child, recursive=False)
        dialog = AppLauncherDialog(EXE, None)
        self._select_source(dialog, "folder", self._source_path("folder", str(child)))
        dialog._app_table.item(0, 1).setText("dp")
        self.qt_app.processEvents()

        with patch(
            "ore_filer.gui.app_launcher_dialog.QFileDialog.getExistingDirectory",
            return_value=str(parent),
        ), patch(
            "ore_filer.gui.app_launcher_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch("ore_filer.gui.app_launcher_dialog.QMessageBox.information") as information:
            dialog._add_folder()

        sources = load_registry(EXE)[1]
        self.assertEqual([(s.path, s.recursive) for s in sources], [(str(parent.resolve()), True)])
        apps = [a for a in load_registry(EXE)[0] if a.name == "deep"]
        self.assertEqual(len(apps), 1)
        self.assertEqual(apps[0].aliases, ("dp",))
        information.assert_called_once()

    def test_child_folder_under_recursive_parent_is_not_registered(self) -> None:
        parent = Path(self._tmp) / "apps"
        child = parent / "sub"
        child.mkdir(parents=True)
        (parent / "top.exe").write_bytes(b"MZ")
        (child / "deep.exe").write_bytes(b"MZ")
        scan_application_directory(EXE, parent, recursive=True)
        dialog = AppLauncherDialog(EXE, None)
        with patch(
            "ore_filer.gui.app_launcher_dialog.QFileDialog.getExistingDirectory",
            return_value=str(child),
        ), patch(
            "ore_filer.gui.app_launcher_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch("ore_filer.gui.app_launcher_dialog.QMessageBox.information"):
            dialog._add_folder()

        sources = load_registry(EXE)[1]
        self.assertEqual([(s.path, s.recursive) for s in sources], [(str(parent.resolve()), True)])
        self.assertIn("deep", self._names())

    def test_remove_is_kept_when_not_confirmed(self) -> None:
        folder = self._make_folder_with_exe("apps", "tool.exe")
        scan_application_directory(EXE, folder, recursive=False)
        dialog = AppLauncherDialog(EXE, None)
        self._select_source(dialog, "folder", self._source_path("folder", str(folder)))
        with patch(
            "ore_filer.gui.app_launcher_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            dialog._remove_selected()
        self.assertIn("tool", self._names())

    def test_removing_folder_source_deletes_its_apps(self) -> None:
        folder = Path(self._tmp) / "apps"
        folder.mkdir()
        (folder / "top.exe").write_bytes(b"MZ")
        scan_application_directory(EXE, folder, recursive=False)
        dialog = AppLauncherDialog(EXE, None)
        self._select_source(dialog, "folder", self._source_path("folder", str(folder)))
        with patch(
            "ore_filer.gui.app_launcher_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dialog._remove_selected()
        self.assertNotIn("top", self._names())
        self.assertEqual([s.path for s in load_registry(EXE)[1] if s.path == str(folder)], [])

    def test_add_folder_registers_non_recursively_when_declined(self) -> None:
        folder = Path(self._tmp) / "apps"
        (folder / "sub").mkdir(parents=True)
        (folder / "top.exe").write_bytes(b"MZ")
        (folder / "sub" / "deep.exe").write_bytes(b"MZ")
        dialog = AppLauncherDialog(EXE, None)
        with patch(
            "ore_filer.gui.app_launcher_dialog.QFileDialog.getExistingDirectory",
            return_value=str(folder),
        ), patch(
            "ore_filer.gui.app_launcher_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            dialog._add_folder()
        names = self._names()
        self.assertIn("top", names)
        self.assertNotIn("deep", names)
        self.assertFalse(load_registry(EXE)[1][0].recursive)

    def test_add_folder_registers_recursively_when_accepted(self) -> None:
        folder = Path(self._tmp) / "apps"
        (folder / "sub").mkdir(parents=True)
        (folder / "top.exe").write_bytes(b"MZ")
        (folder / "sub" / "deep.exe").write_bytes(b"MZ")
        dialog = AppLauncherDialog(EXE, None)
        with patch(
            "ore_filer.gui.app_launcher_dialog.QFileDialog.getExistingDirectory",
            return_value=str(folder),
        ), patch(
            "ore_filer.gui.app_launcher_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dialog._add_folder()
        names = self._names()
        self.assertIn("top", names)
        self.assertIn("deep", names)
        self.assertTrue(load_registry(EXE)[1][0].recursive)


class KeymapTests(unittest.TestCase):
    def test_open_with_app_uses_l(self) -> None:
        self.assertEqual(DEFAULT_BINDINGS["open_with_app"], "L")

    def test_default_bindings_have_no_duplicates(self) -> None:
        values = list(DEFAULT_BINDINGS.values())
        self.assertEqual(len(values), len(set(values)))


if __name__ == "__main__":
    unittest.main()
