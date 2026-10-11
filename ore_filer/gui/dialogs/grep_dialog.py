from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ore_filer.gui.threads import GrepThread
from ore_filer.services.search_service import GrepMatch


class GrepDialog(QDialog):
    def __init__(self, root: str | Path, parent=None):
        super().__init__(parent)
        self.root = Path(root).expanduser().resolve()
        self._thread: GrepThread | None = None
        self._results: list[GrepMatch] = []
        self._closing = False

        self.setWindowTitle("テキスト GREP")
        self.resize(800, 560)

        self.pattern_edit = QLineEdit(self)
        self.pattern_edit.setPlaceholderText("検索パターン（正規表現も使用可）")
        self.search_btn = QPushButton("検索", self)
        self.search_btn.clicked.connect(self._start_search)
        self.pattern_edit.returnPressed.connect(self._start_search)

        search_row = QHBoxLayout()
        search_row.addWidget(self.pattern_edit)
        search_row.addWidget(self.search_btn)

        self.recursive_cb = QCheckBox("サブディレクトリを含む", self)
        self.recursive_cb.setChecked(True)
        self.regex_cb = QCheckBox("正規表現", self)
        self.ignorecase_cb = QCheckBox("大文字小文字を区別しない", self)
        self.ignorecase_cb.setChecked(True)

        opt_row = QHBoxLayout()
        opt_row.addWidget(self.recursive_cb)
        opt_row.addWidget(self.regex_cb)
        opt_row.addWidget(self.ignorecase_cb)
        opt_row.addStretch()

        self.status_label = QLabel(f"検索場所: {self.root}", self)
        self.result_list = QListWidget(self)
        self.result_list.setAlternatingRowColors(True)
        self.result_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.result_list.itemDoubleClicked.connect(self._accept_selected)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setEnabled(False)
        self.buttons.accepted.connect(self._accept_selected)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addLayout(opt_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.result_list, 1)
        layout.addWidget(self.buttons)

        self.pattern_edit.setFocus()

    def _start_search(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        pattern = self.pattern_edit.text().strip()
        if not pattern:
            self.status_label.setText("パターンを入力してください")
            return

        self._results = []
        self.result_list.clear()
        self.ok_btn.setEnabled(False)
        self.pattern_edit.setEnabled(False)
        self.search_btn.setEnabled(False)
        self.status_label.setText("検索中...")

        thread = GrepThread(
            self.root, pattern,
            recursive=self.recursive_cb.isChecked(),
            use_regex=self.regex_cb.isChecked(),
            ignore_case=self.ignorecase_cb.isChecked(),
            parent=self,
        )
        thread.finished_grep.connect(self._on_finished)
        thread.error.connect(self._on_error)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        thread.start()

    def _on_finished(self, results: list) -> None:
        if self._closing:
            return
        self._results = list(results)
        # ファイル単位でまとめて表示（cfiler 準拠）
        seen: dict = {}
        for m in results:
            if m.path not in seen:
                seen[m.path] = 0
            seen[m.path] += 1

        for path, count in seen.items():
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                rel = path
            item = QListWidgetItem(f"{rel}  ({count} 件)")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.result_list.addItem(item)

        if seen:
            self.result_list.setCurrentRow(0)
            self.ok_btn.setEnabled(True)
            self.status_label.setText(
                f"{len(seen)} ファイル / {len(results)} マッチ"
            )
        else:
            self.status_label.setText("見つかりませんでした")

    def _on_error(self, msg: str) -> None:
        if not self._closing:
            self.status_label.setText(f"エラー: {msg}")

    def _on_thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        if thread:
            thread.deleteLater()
        if self._closing:
            self.done(QDialog.DialogCode.Rejected)
            return
        self.pattern_edit.setEnabled(True)
        self.search_btn.setEnabled(True)

    def _accept_selected(self, _item=None) -> None:
        item = self.result_list.currentItem()
        if item is None:
            return
        self.accept()

    def matched_paths(self) -> list[Path]:
        """マッチしたファイルパス一覧（重複なし）を返す。"""
        seen: list[Path] = []
        seen_set: set[Path] = set()
        for m in self._results:
            if m.path not in seen_set:
                seen_set.add(m.path)
                seen.append(m.path)
        return seen

    def line_hits(self) -> dict[Path, list[int]]:
        """ファイルパス → 該当行番号リスト の辞書を返す。"""
        result: dict[Path, list[int]] = {}
        for m in self._results:
            result.setdefault(m.path, []).append(m.lineno)
        return result

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._closing = True
            self.status_label.setText("検索を中止しています...")
            self.pattern_edit.setEnabled(False)
            self.search_btn.setEnabled(False)
            self.ok_btn.setEnabled(False)
            self._thread.requestInterruption()
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._thread is not None and self._thread.isRunning():
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)
