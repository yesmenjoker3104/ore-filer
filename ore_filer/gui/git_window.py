"""Git 操作専用ウィンドウ。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ore_filer.gui.dialogs import (
    DiffDialog,
    GitCommandThread,
    GitCommitDialog,
    GitHttpCredentialsDialog,
    GitLogDialog,
    SideBySideDiffDialog,
)
from ore_filer.services import git_service

_STATUS_COLORS: dict[str, str] = {
    "staged":    "#4caf50",
    "unstaged":  "#ff9800",
    "untracked": "#9e9e9e",
    "deleted":   "#f44336",
    "conflict":  "#e91e63",
}


def _classify(xy: str) -> str:
    if xy in ("??", "!!"):
        return "untracked"
    if len(xy) < 2:
        return "unstaged"
    x, y = xy[0], xy[1]
    if x in ("U",) or y in ("U",) or xy in ("AA", "DD"):
        return "conflict"
    if x == "D" or y == "D":
        return "deleted"
    if x not in (" ", "?", "!"):
        return "staged"
    return "unstaged"


class GitWindow(QDialog):
    """Git 操作専用ウィンドウ。モードレスで使用する。"""

    repo_changed = Signal()

    def __init__(self, repo: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle("Git")
        self.resize(720, 520)
        self._repo = repo
        self._threads: list = []

        self._header_label = QLabel(self)
        self._header_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._tree = QTreeWidget(self)
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["状態", "ファイル"])
        self._tree.setColumnWidth(0, 55)
        self._tree.header().setStretchLastSection(True)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self._tree.setAlternatingRowColors(True)
        self._tree.setRootIsDecorated(False)

        row1 = QHBoxLayout()
        self._btn_stage   = self._make_btn("ステージ (a)",     self._stage)
        self._btn_unstage = self._make_btn("ステージ解除 (r)", self._unstage)
        self._btn_commit  = self._make_btn("コミット... (c)",  self._commit)
        for btn in (self._btn_stage, self._btn_unstage, self._btn_commit):
            row1.addWidget(btn)
        row1.addStretch()

        row2 = QHBoxLayout()
        self._btn_fetch = self._make_btn("Fetch (f)", self._fetch)
        self._btn_pull  = self._make_btn("Pull (l)",  self._pull)
        self._btn_push  = self._make_btn("Push (p)",  self._push)
        for btn in (self._btn_fetch, self._btn_pull, self._btn_push):
            row2.addWidget(btn)
        row2.addStretch()

        row3 = QHBoxLayout()
        self._btn_diff    = self._make_btn("差分 (d)",        self._diff)
        self._btn_log     = self._make_btn("ログ (g)",         self._log)
        self._btn_branch  = self._make_btn("ブランチ切替 (b)", self._switch_branch)
        self._btn_refresh = self._make_btn("更新 (F5)",        self._refresh)
        for btn in (self._btn_diff, self._btn_log, self._btn_branch, self._btn_refresh):
            row3.addWidget(btn)
        row3.addStretch()

        self._status_label = QLabel(self)

        layout = QVBoxLayout(self)
        layout.addWidget(self._header_label)
        layout.addWidget(self._tree, 1)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addLayout(row3)
        layout.addWidget(self._status_label)

        for key, fn in (
            ("a",  self._stage),
            ("r",  self._unstage),
            ("c",  self._commit),
            ("f",  self._fetch),
            ("l",  self._pull),
            ("p",  self._push),
            ("d",  self._diff),
            ("g",  self._log),
            ("b",  self._switch_branch),
            ("F5", self._refresh),
        ):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(fn)

        self._refresh()

    # ── ヘルパー ──────────────────────────────────────────────

    def _make_btn(self, text: str, slot) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        btn.clicked.connect(slot)
        return btn

    def _set_status(self, msg: str) -> None:
        self._status_label.setText(msg)

    def _set_busy(self, busy: bool) -> None:
        for btn in (
            self._btn_stage, self._btn_unstage, self._btn_commit,
            self._btn_fetch, self._btn_pull, self._btn_push,
            self._btn_diff, self._btn_log, self._btn_branch,
        ):
            btn.setEnabled(not busy)

    def _selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for item in self._tree.selectedItems():
            raw = item.data(0, Qt.ItemDataRole.UserRole)
            if raw:
                paths.append(Path(raw))
        return paths

    def _run_thread(self, thread, busy_msg: str, done_fn=None) -> None:
        self._threads.append(thread)
        self._set_busy(True)
        self._set_status(busy_msg)

        def on_done(out=""):
            self._set_busy(False)
            if done_fn:
                done_fn(out)
            if thread in self._threads:
                self._threads.remove(thread)

        def on_fail(err):
            self._set_busy(False)
            self._set_status(f"エラー: {err}")
            QMessageBox.warning(self, "Git エラー", err)
            if thread in self._threads:
                self._threads.remove(thread)

        thread.succeeded.connect(on_done)
        thread.failed.connect(on_fail)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _http_credentials(self) -> tuple[str, str] | None:
        from ore_filer import credentials as cred_store
        url = git_service.get_remote_url(self._repo)
        if not (url and url.startswith(("http://", "https://"))):
            return ("", "")
        saved = cred_store.load("git", url)
        username, password = saved if saved else ("", "")
        dlg = GitHttpCredentialsDialog(url, username=username, password=password, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        username, password = dlg.credentials()
        if dlg.should_save():
            cred_store.save("git", url, username, password)
        else:
            cred_store.delete("git", url)
        return username, password

    # ── リフレッシュ ───────────────────────────────────────────

    def _refresh(self) -> None:
        try:
            branch = git_service.current_branch(self._repo)
            status_map = git_service.status(self._repo)
        except Exception as e:
            self._set_status(f"ステータス取得失敗: {e}")
            return

        self._tree.clear()
        count = 0
        for abs_path, xy in sorted(status_map.items(), key=lambda kv: str(kv[0])):
            if xy == "D~":
                continue
            try:
                rel = str(abs_path.relative_to(self._repo))
            except ValueError:
                rel = abs_path.name

            color = QColor(_STATUS_COLORS.get(_classify(xy), "#cccccc"))
            item = QTreeWidgetItem([xy, rel])
            item.setForeground(0, color)
            item.setForeground(1, color)
            item.setData(0, Qt.ItemDataRole.UserRole, str(abs_path))
            self._tree.addTopLevelItem(item)
            count += 1

        self._header_label.setText(
            f"リポジトリ: {self._repo}　　ブランチ: {branch}"
        )
        self.setWindowTitle(f"Git — {branch}")
        self._set_status(f"{count} 件の変更" if count else "変更なし")

    # ── 操作 ──────────────────────────────────────────────────

    def _stage(self) -> None:
        paths = self._selected_paths()
        if not paths:
            self._set_status("ステージするファイルを選択してください")
            return
        try:
            git_service.stage(self._repo, paths)
        except Exception as e:
            QMessageBox.warning(self, "Git ステージ", str(e))
            return
        self._refresh()
        self.repo_changed.emit()
        self._set_status(f"{len(paths)} 件をステージしました")

    def _unstage(self) -> None:
        paths = self._selected_paths()
        if not paths:
            self._set_status("ステージ解除するファイルを選択してください")
            return
        try:
            git_service.unstage(self._repo, paths)
        except Exception as e:
            QMessageBox.warning(self, "Git ステージ解除", str(e))
            return
        self._refresh()
        self.repo_changed.emit()
        self._set_status(f"{len(paths)} 件のステージを解除しました")

    def _commit(self) -> None:
        try:
            raw_map = git_service.status(self._repo)
        except Exception as e:
            QMessageBox.warning(self, "Git コミット", str(e))
            return

        staged: list[str] = []
        for path, xy in raw_map.items():
            if len(xy) >= 1 and xy[0] not in (" ", "?", "!") and xy != "D~" and path.is_file():
                try:
                    staged.append(str(path.relative_to(self._repo)))
                except ValueError:
                    staged.append(path.name)

        if not staged:
            QMessageBox.information(self, "Git コミット", "ステージされた変更がありません。")
            return

        dialog = GitCommitDialog(staged, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        msg = dialog.commit_message()
        if not msg:
            return

        thread = GitCommandThread(
            lambda r=self._repo, m=msg: git_service.commit(r, m), self
        )

        def on_done(out: str) -> None:
            self._refresh()
            self.repo_changed.emit()
            self._set_status(f"コミット完了: {out}")

        self._run_thread(thread, "コミット中...", done_fn=on_done)

    def _fetch(self) -> None:
        creds = self._http_credentials()
        if creds is None:
            return
        username, password = creds
        thread = GitCommandThread(
            lambda r=self._repo, u=username, p=password: git_service.fetch(
                r, username=u or None, password=p or None
            ),
            self,
        )

        def on_done(out: str) -> None:
            self._refresh()
            self._set_status("fetch 完了")
            if out.strip():
                QMessageBox.information(self, "git fetch", out.strip())

        self._run_thread(thread, "fetch 中...", done_fn=on_done)

    def _pull(self) -> None:
        creds = self._http_credentials()
        if creds is None:
            return
        username, password = creds
        thread = GitCommandThread(
            lambda r=self._repo, u=username, p=password: git_service.pull(
                r, username=u or None, password=p or None
            ),
            self,
        )

        def on_done(out: str) -> None:
            self._refresh()
            self.repo_changed.emit()
            self._set_status("pull 完了")
            if out.strip():
                QMessageBox.information(self, "git pull", out.strip())

        self._run_thread(thread, "pull 中...", done_fn=on_done)

    def _push(self) -> None:
        creds = self._http_credentials()
        if creds is None:
            return
        username, password = creds
        thread = GitCommandThread(
            lambda r=self._repo, u=username, p=password: git_service.push(
                r, username=u or None, password=p or None
            ),
            self,
        )

        def on_done(out: str) -> None:
            self._refresh()
            self._set_status("push 完了")
            if out.strip():
                QMessageBox.information(self, "git push", out.strip())

        self._run_thread(thread, "push 中...", done_fn=on_done)

    def _diff(self) -> None:
        paths = self._selected_paths()
        if not paths:
            self._set_status("差分を表示するファイルを選択してください")
            return
        path = paths[0]
        if not path.is_file():
            self._set_status("ファイルを選択してください（ディレクトリ不可）")
            return
        try:
            head_text = git_service.head_file_text(self._repo, path)
            work_text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            QMessageBox.warning(self, "Git 差分", str(e))
            return
        SideBySideDiffDialog("HEAD", path.name, head_text, work_text, self).exec()

    def _log(self) -> None:
        try:
            entries = git_service.log(self._repo)
        except Exception as e:
            QMessageBox.warning(self, "Git ログ", str(e))
            return
        if not entries:
            QMessageBox.information(self, "Git ログ", "コミット履歴がありません。")
            return

        dialog = GitLogDialog(entries, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            commit_hash = dialog.selected_commit()
            if commit_hash:
                try:
                    diff_text = git_service.show(self._repo, commit_hash)
                    DiffDialog(commit_hash, "show", diff_text, self).exec()
                except Exception as e:
                    QMessageBox.warning(self, "git show", str(e))

    def _switch_branch(self) -> None:
        try:
            branch_list = git_service.branches(self._repo)
        except Exception as e:
            QMessageBox.warning(self, "ブランチ切替", str(e))
            return
        if not branch_list:
            QMessageBox.information(self, "ブランチ切替", "ブランチが見つかりません。")
            return

        current = branch_list[0]
        selected, ok = QInputDialog.getItem(
            self, "ブランチ切替", "切り替え先のブランチ:", branch_list, 0, False,
        )
        if not ok or selected == current:
            return
        try:
            git_service.switch(self._repo, selected)
        except Exception as e:
            QMessageBox.warning(self, "ブランチ切替", f"切り替えに失敗しました:\n{e}")
            return
        self._refresh()
        self.repo_changed.emit()
        self._set_status(f"ブランチを '{selected}' に切り替えました")

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
