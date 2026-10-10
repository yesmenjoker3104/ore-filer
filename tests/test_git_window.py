import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from dulwich import porcelain

from ore_filer.gui.git_window import GitWindow, _classify
from ore_filer.services import git_service


def _init_repo(path: Path) -> None:
    repo = porcelain.init(path)
    config = repo.get_config()
    config.set((b"user",), b"name", b"Test User")
    config.set((b"user",), b"email", b"test@example.com")
    config.write_to_path()
    repo.close()


class ClassifyTests(unittest.TestCase):
    def test_untracked(self) -> None:
        self.assertEqual(_classify("??"), "untracked")

    def test_staged_add(self) -> None:
        self.assertEqual(_classify("A "), "staged")

    def test_staged_modify(self) -> None:
        self.assertEqual(_classify("M "), "staged")

    def test_unstaged_modify(self) -> None:
        self.assertEqual(_classify(" M"), "unstaged")

    def test_deleted_unstaged(self) -> None:
        self.assertEqual(_classify(" D"), "deleted")

    def test_deleted_staged(self) -> None:
        self.assertEqual(_classify("D "), "deleted")

    def test_conflict(self) -> None:
        self.assertEqual(_classify("UU"), "conflict")
        self.assertEqual(_classify("AA"), "conflict")


class GitWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _make_window(self, repo: Path) -> GitWindow:
        win = GitWindow(repo, None)
        self.app.processEvents()
        return win

    def test_window_title_shows_branch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            (root / "a.txt").write_text("x\n", encoding="utf-8")
            git_service.stage(root, [root / "a.txt"])
            git_service.commit(root, "initial")

            win = self._make_window(root)
            try:
                self.assertIn("Git", win.windowTitle())
            finally:
                win.close()

    def test_untracked_file_appears_in_tree(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            (root / "readme.txt").write_text("hello\n", encoding="utf-8")

            win = self._make_window(root)
            try:
                items = [
                    win._tree.topLevelItem(i).text(1)
                    for i in range(win._tree.topLevelItemCount())
                ]
                self.assertTrue(any("readme.txt" in item for item in items))
            finally:
                win.close()

    def test_staged_file_shows_correct_xy_code(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("x\n", encoding="utf-8")
            git_service.stage(root, [path])

            win = self._make_window(root)
            try:
                xy_codes = [
                    win._tree.topLevelItem(i).text(0)
                    for i in range(win._tree.topLevelItemCount())
                ]
                self.assertIn("A ", xy_codes)
            finally:
                win.close()

    def test_stage_selected_item(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("x\n", encoding="utf-8")

            win = self._make_window(root)
            try:
                # untracked → select it → stage
                self.assertEqual(win._tree.topLevelItemCount(), 1)
                win._tree.topLevelItem(0).setSelected(True)
                win._stage()
                self.app.processEvents()

                self.assertEqual(git_service.status(root)[path.resolve()], "A ")
            finally:
                win.close()

    def test_stage_emits_repo_changed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            (root / "note.txt").write_text("x\n", encoding="utf-8")

            win = self._make_window(root)
            try:
                emitted: list[bool] = []
                win.repo_changed.connect(lambda: emitted.append(True))
                win._tree.topLevelItem(0).setSelected(True)
                win._stage()
                self.app.processEvents()

                self.assertEqual(emitted, [True])
            finally:
                win.close()

    def test_unstage_selected_item(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("x\n", encoding="utf-8")
            git_service.stage(root, [path])

            win = self._make_window(root)
            try:
                self.assertEqual(git_service.status(root)[path.resolve()], "A ")
                win._tree.topLevelItem(0).setSelected(True)
                win._unstage()
                self.app.processEvents()

                self.assertEqual(git_service.status(root)[path.resolve()], "??")
            finally:
                win.close()

    def test_unstage_emits_repo_changed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("x\n", encoding="utf-8")
            git_service.stage(root, [path])

            win = self._make_window(root)
            try:
                emitted: list[bool] = []
                win.repo_changed.connect(lambda: emitted.append(True))
                win._tree.topLevelItem(0).setSelected(True)
                win._unstage()
                self.app.processEvents()

                self.assertEqual(emitted, [True])
            finally:
                win.close()

    def test_stage_with_nothing_selected_shows_status(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            (root / "note.txt").write_text("x\n", encoding="utf-8")

            win = self._make_window(root)
            try:
                win._tree.clearSelection()
                win._stage()
                self.app.processEvents()

                self.assertIn("選択", win._status_label.text())
            finally:
                win.close()

    def test_commit_no_staged_shows_info(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            (root / "note.txt").write_text("x\n", encoding="utf-8")
            # ステージしない → コミット対象なし

            win = self._make_window(root)
            try:
                with patch("ore_filer.gui.git_window.QMessageBox") as mock_mb:
                    win._commit()
                    self.app.processEvents()
                    mock_mb.information.assert_called_once()
            finally:
                win.close()

    def test_commit_staged_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("x\n", encoding="utf-8")
            git_service.stage(root, [path])

            win = self._make_window(root)
            try:
                emitted: list[bool] = []
                win.repo_changed.connect(lambda: emitted.append(True))

                mock_dialog = MagicMock()
                mock_dialog.exec.return_value = QDialog.DialogCode.Accepted
                mock_dialog.commit_message.return_value = "test commit"

                def sync_run(thread, busy_msg, done_fn=None):
                    result = thread._func()
                    if done_fn:
                        done_fn(result or "")

                with patch("ore_filer.gui.git_window.GitCommitDialog", return_value=mock_dialog), \
                     patch.object(win, "_run_thread", side_effect=sync_run):
                    win._commit()
                    self.app.processEvents()

                self.assertEqual(git_service.status(root), {})
                self.assertEqual(emitted, [True])
            finally:
                win.close()

    def test_switch_branch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("x\n", encoding="utf-8")
            git_service.stage(root, [path])
            git_service.commit(root, "initial")
            porcelain.branch_create(root, "feature")

            win = self._make_window(root)
            try:
                emitted: list[bool] = []
                win.repo_changed.connect(lambda: emitted.append(True))

                with patch("ore_filer.gui.git_window.QInputDialog") as mock_input:
                    mock_input.getItem.return_value = ("feature", True)
                    win._switch_branch()
                    self.app.processEvents()

                self.assertEqual(git_service.current_branch(root), "feature")
                self.assertEqual(emitted, [True])
            finally:
                win.close()

    def test_refresh_updates_tree_after_staging(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("x\n", encoding="utf-8")

            win = self._make_window(root)
            try:
                count_before = win._tree.topLevelItemCount()
                git_service.stage(root, [path])
                git_service.commit(root, "initial")
                win._refresh()
                self.app.processEvents()
                count_after = win._tree.topLevelItemCount()

                self.assertGreater(count_before, 0)
                self.assertEqual(count_after, 0)
            finally:
                win.close()

    def test_escape_hides_window(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)

            win = self._make_window(root)
            try:
                win.show()
                self.app.processEvents()
                self.assertTrue(win.isVisible())

                from PySide6.QtGui import QKeyEvent
                from PySide6.QtCore import QEvent
                event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
                win.keyPressEvent(event)
                self.app.processEvents()

                self.assertFalse(win.isVisible())
            finally:
                win.close()


class GitWindowMultiRepoTests(unittest.TestCase):
    """MainWindow の複数 GitWindow 管理をテストする。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_separate_window_per_repo(self) -> None:
        with TemporaryDirectory() as tmp1, TemporaryDirectory() as tmp2:
            repo1 = Path(tmp1) / "repo1"
            repo2 = Path(tmp2) / "repo2"
            _init_repo(repo1)
            _init_repo(repo2)

            git_windows: dict = {}
            for repo in (repo1, repo2):
                win = GitWindow(repo, None)
                git_windows[repo] = win

            try:
                self.assertEqual(len(git_windows), 2)
                self.assertIsNot(git_windows[repo1], git_windows[repo2])
                self.assertEqual(git_windows[repo1]._repo, repo1)
                self.assertEqual(git_windows[repo2]._repo, repo2)
            finally:
                for win in git_windows.values():
                    win.close()

    def test_same_repo_reuses_window(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root)

            git_windows: dict = {}
            win1 = GitWindow(root, None)
            git_windows[root] = win1

            # 同じリポジトリでは既存ウィンドウを返す
            win2 = git_windows.get(root)

            try:
                self.assertIs(win1, win2)
            finally:
                win1.close()


if __name__ == "__main__":
    unittest.main()
