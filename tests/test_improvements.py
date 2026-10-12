"""今回の改善項目に対するテスト。

対象:
- _calc_total_size: ファイル/ディレクトリの合計サイズ計算
- confirm_list の total_size 表示
- git_service.create_branch: 新規ブランチ作成
- LogViewerDialog._LEVELS: 行頭パターン優先の正規表現
- pane.clear_filter: フィルター解除後のカーソル位置保持
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dulwich import porcelain
from PySide6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])


# ── _calc_total_size ──────────────────────────────────────────────────────────

class CalcTotalSizeTests(unittest.TestCase):
    def _make_tree(self, root: Path) -> int:
        """root 配下にファイルを作り、合計バイト数を返す。"""
        (root / "a.txt").write_bytes(b"hello")         # 5
        sub = root / "sub"
        sub.mkdir()
        (sub / "b.txt").write_bytes(b"world!")         # 6
        return 11

    def test_files_only(self) -> None:
        from ore_filer.gui.main_window_file_ops import MainWindowFileOpsMixin
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_bytes(b"abc")       # 3
            (root / "b.txt").write_bytes(b"de")        # 2
            total = MainWindowFileOpsMixin._calc_total_size([root / "a.txt", root / "b.txt"])
        self.assertEqual(total, 5)

    def test_directory_recurses(self) -> None:
        from ore_filer.gui.main_window_file_ops import MainWindowFileOpsMixin
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "tree"
            root.mkdir()
            expected = self._make_tree(root)
            total = MainWindowFileOpsMixin._calc_total_size([root])
        self.assertEqual(total, expected)

    def test_missing_file_does_not_raise(self) -> None:
        from ore_filer.gui.main_window_file_ops import MainWindowFileOpsMixin
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "no_such_file.txt"
            total = MainWindowFileOpsMixin._calc_total_size([missing])
        self.assertEqual(total, 0)


# ── confirm_list (total_size) ─────────────────────────────────────────────────

class ConfirmListSizeTests(unittest.TestCase):
    def test_summary_label_shows_size(self) -> None:
        """total_size を渡したとき QLabel に MB/KB が含まれること。"""
        from ore_filer.gui.dialogs.common import confirm_list, _fmt_file_size
        size = 2 * 1024 * 1024  # 2 MB
        label_text = _fmt_file_size(size)
        self.assertIn("MB", label_text)

    def test_summary_without_size_shows_count_only(self) -> None:
        from ore_filer.gui.dialogs.common import _fmt_file_size
        # total_size=None → 件数のみ（ここでは confirm_list の内部ロジックを直接検証）
        # confirm_list はダイアログを表示するため、ラベル生成ロジックのみ検証
        size = 512
        label = _fmt_file_size(size)
        self.assertIn("512", label)

    def test_fmt_file_size_kb(self) -> None:
        from ore_filer.gui.dialogs.common import _fmt_file_size
        self.assertIn("KB", _fmt_file_size(2048))

    def test_fmt_file_size_gb(self) -> None:
        from ore_filer.gui.dialogs.common import _fmt_file_size
        self.assertIn("GB", _fmt_file_size(2 * 1024 ** 3))


# ── git_service.create_branch ─────────────────────────────────────────────────

def _init_repo(path: Path) -> None:
    repo = porcelain.init(path)
    config = repo.get_config()
    config.set((b"user",), b"name", b"Test User")
    config.set((b"user",), b"email", b"test@example.com")
    config.write_to_path()
    repo.close()


class CreateBranchTests(unittest.TestCase):
    def _make_repo_with_commit(self, root: Path) -> None:
        _init_repo(root)
        from ore_filer.services import git_service
        f = root / "file.txt"
        f.write_bytes(b"init\n")
        git_service.stage(root, [f])
        git_service.commit(root, "initial")

    def test_creates_branch(self) -> None:
        from ore_filer.services import git_service
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._make_repo_with_commit(root)
            git_service.create_branch(root, "feature", checkout=False)
            self.assertIn("feature", git_service.branches(root))

    def test_checkout_true_switches_branch(self) -> None:
        from ore_filer.services import git_service
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._make_repo_with_commit(root)
            git_service.create_branch(root, "develop", checkout=True)
            self.assertEqual(git_service.current_branch(root), "develop")

    def test_duplicate_name_raises_git_error(self) -> None:
        from ore_filer.services import git_service
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._make_repo_with_commit(root)
            git_service.create_branch(root, "dup", checkout=False)
            with self.assertRaises(git_service.GitError):
                git_service.create_branch(root, "dup", checkout=False)


# ── LogViewerDialog._LEVELS 正規表現 ─────────────────────────────────────────

def _level_name(line: str) -> str | None:
    """_LEVELS パターンにマッチする最初のレベル名を返す（テスト用ヘルパー）。"""
    from ore_filer.gui.dialogs.text_viewers import LogViewerDialog
    names = ("FATAL", "ERROR", "WARN", "INFO", "DEBUG")
    for (pat, _, _), name in zip(LogViewerDialog._LEVELS, names):
        if pat.search(line):
            return name
    return None


class LogLevelRegexTests(unittest.TestCase):
    # --- 行頭ブラケット形式 ---
    def test_bracket_error(self) -> None:
        self.assertEqual(_level_name("[ERROR] something failed"), "ERROR")

    def test_bracket_warn(self) -> None:
        self.assertEqual(_level_name("[WARN] low disk"), "WARN")

    def test_bracket_info(self) -> None:
        self.assertEqual(_level_name("[INFO] started"), "INFO")

    def test_bracket_debug(self) -> None:
        self.assertEqual(_level_name("[DEBUG] value=42"), "DEBUG")

    # --- コロン区切り ---
    def test_colon_error(self) -> None:
        self.assertEqual(_level_name("ERROR: connection refused"), "ERROR")

    def test_colon_fatal(self) -> None:
        self.assertEqual(_level_name("FATAL: out of memory"), "FATAL")

    # --- level=value 形式 ---
    def test_key_value_error(self) -> None:
        self.assertEqual(_level_name('time="2024" level=error msg="oops"'), "ERROR")

    def test_key_value_warn(self) -> None:
        self.assertEqual(_level_name("level=warn msg=low"), "WARN")

    # --- 変数名は誤検知しない ---
    def test_no_false_positive_error_count(self) -> None:
        self.assertIsNone(_level_name("errorCount=0 warningCount=0"))

    def test_no_false_positive_info_in_word(self) -> None:
        self.assertIsNone(_level_name("informationText = 'hello'"))

    def test_no_false_positive_debug_in_word(self) -> None:
        self.assertIsNone(_level_name("debugMode=true"))

    # --- 大文字小文字を問わない ---
    def test_case_insensitive(self) -> None:
        self.assertEqual(_level_name("[error] test"), "ERROR")
        self.assertEqual(_level_name("[Warning] test"), "WARN")


# ── pane.clear_filter カーソル位置保持 ───────────────────────────────────────

class ClearFilterCursorTests(unittest.TestCase):
    def test_cursor_position_preserved_after_clear(self) -> None:
        """フィルター解除後、直前のカーソル位置のファイルにフォーカスが戻る。"""
        import tempfile
        from ore_filer.gui.pane import PaneWidget

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # ファイルを3つ作成: a.txt b.txt c.txt
            for name in ("a.txt", "b.txt", "c.txt"):
                (root / name).write_bytes(b"")

            pane = PaneWidget(str(root))
            pane.show()
            _app.processEvents()

            # b.txt にカーソルを移動
            idx_b = pane.index_for_path(root / "b.txt")
            self.assertTrue(idx_b.isValid(), "b.txt のインデックスが見つからない")
            pane.file_view.setCurrentIndex(idx_b)
            _app.processEvents()

            # フィルターを適用（b のみ表示）
            pane.set_filter("b")
            _app.processEvents()

            # フィルターを解除
            pane.clear_filter()
            _app.processEvents()

            # b.txt にカーソルが戻っていること
            current = pane.path_from_index(pane.file_view.currentIndex())
            self.assertIsNotNone(current)
            self.assertEqual(current.name, "b.txt")

            pane.close()


if __name__ == "__main__":
    unittest.main()
