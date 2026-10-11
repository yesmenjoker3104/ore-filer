from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

# ── 一括リネーム ──────────────────────────────────────────

_INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _validate_new_name(name: str) -> str | None:
    """問題があれば理由文字列を返し、OK なら None を返す。"""
    if not name or not name.strip():
        return "空白のみ"
    if _INVALID_NAME_CHARS.search(name):
        return "使えない文字を含む"
    return None


class BulkRenameDialog(QDialog):
    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self.setWindowTitle("一括リネーム")
        self.resize(760, 560)
        self._paths = list(paths)
        self._result_pairs: list[tuple[Path, str]] = []

        # ── モード選択 ──
        self._mode_replace = QRadioButton("置換", self)
        self._mode_replace.setChecked(True)
        self._mode_seq = QRadioButton("連番", self)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._mode_replace)
        mode_group.addButton(self._mode_seq)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._mode_replace)
        mode_row.addWidget(self._mode_seq)
        mode_row.addStretch()

        # ── 置換パネル ──
        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("検索文字列")
        self._replace_edit = QLineEdit(self)
        self._replace_edit.setPlaceholderText("置換文字列")
        self._regex_check = QCheckBox("正規表現", self)
        replace_form = QFormLayout()
        replace_form.addRow("検索:", self._search_edit)
        replace_form.addRow("置換:", self._replace_edit)
        replace_form.addRow("", self._regex_check)

        self._replace_panel = QWidget(self)
        self._replace_panel.setLayout(replace_form)

        # ── 連番パネル ──
        self._template_edit = QLineEdit("{name}_{n:03}{ext}", self)
        self._template_edit.setPlaceholderText("{name}_{n:03}{ext}")
        self._start_spin = QSpinBox(self)
        self._start_spin.setRange(0, 99999)
        self._start_spin.setValue(1)
        seq_form = QFormLayout()
        seq_form.addRow("書式:", self._template_edit)
        seq_form.addRow("開始番号:", self._start_spin)
        hint = QLabel(
            "変数: {name}=元の名前(拡張子なし), {ext}=拡張子(.付き), {n}=連番, {N}=総数",
            self,
        )
        hint.setWordWrap(True)
        seq_layout = QVBoxLayout()
        seq_layout.addLayout(seq_form)
        seq_layout.addWidget(hint)

        self._seq_panel = QWidget(self)
        self._seq_panel.setLayout(seq_layout)
        self._seq_panel.setVisible(False)

        # ── プレビュー表 ──
        self._table = QTableWidget(len(self._paths), 2, self)
        self._table.setHorizontalHeaderLabels(["変更前", "変更後"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 300)
        for row, path in enumerate(self._paths):
            self._table.setItem(row, 0, QTableWidgetItem(path.name))
            self._table.setItem(row, 1, QTableWidgetItem(path.name))

        # ── ボタン ──
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(mode_row)
        layout.addWidget(self._replace_panel)
        layout.addWidget(self._seq_panel)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._buttons)

        # シグナル接続
        self._mode_replace.toggled.connect(self._on_mode_changed)
        self._search_edit.textChanged.connect(self._update_preview)
        self._replace_edit.textChanged.connect(self._update_preview)
        self._regex_check.toggled.connect(self._update_preview)
        self._template_edit.textChanged.connect(self._update_preview)
        self._start_spin.valueChanged.connect(self._update_preview)

        self._update_preview()

    def _on_mode_changed(self, is_replace: bool) -> None:
        self._replace_panel.setVisible(is_replace)
        self._seq_panel.setVisible(not is_replace)
        self._update_preview()

    def _compute_new_names(self) -> list[str]:
        new_names: list[str] = []
        if self._mode_replace.isChecked():
            search = self._search_edit.text()
            replace = self._replace_edit.text()
            use_regex = self._regex_check.isChecked()
            for path in self._paths:
                try:
                    if use_regex and search:
                        new_name = re.sub(search, replace, path.name)
                    elif search:
                        new_name = path.name.replace(search, replace)
                    else:
                        new_name = path.name
                except re.error:
                    new_name = path.name
                new_names.append(new_name)
        else:
            template = self._template_edit.text()
            start = self._start_spin.value()
            total = len(self._paths)
            for i, path in enumerate(self._paths):
                n = start + i
                try:
                    new_name = template.format(
                        name=path.stem,
                        ext=path.suffix,
                        n=n,
                        N=total,
                    )
                except (KeyError, ValueError):
                    new_name = path.name
                new_names.append(new_name)
        return new_names

    def _update_preview(self) -> None:
        new_names = self._compute_new_names()
        error_color = QBrush(QColor(255, 80, 80, 120))
        ok_color = QBrush(QColor(0, 0, 0, 0))

        # 重複チェック（変更後名同士）
        name_count: dict[str, int] = {}
        for name in new_names:
            name_count[name] = name_count.get(name, 0) + 1

        has_error = False
        existing_names = {p.name.casefold() for p in self._paths}

        for row, (path, new_name) in enumerate(zip(self._paths, new_names)):
            item = self._table.item(row, 1)
            if item is None:
                item = QTableWidgetItem()
                self._table.setItem(row, 1, item)
            item.setText(new_name)

            error = _validate_new_name(new_name)
            if error is None and name_count.get(new_name, 0) > 1:
                error = "重複"
            # 自分以外の既存ファイルと重複しているか（リネーム先が別ファイルと被る）
            if error is None and new_name.casefold() != path.name.casefold():
                parent = path.parent
                if (parent / new_name).exists() and new_name.casefold() not in existing_names:
                    error = "既に存在"

            color = error_color if error else ok_color
            for col in range(2):
                cell = self._table.item(row, col)
                if cell:
                    cell.setBackground(color)
                    if error and col == 1:
                        cell.setToolTip(error)
                    else:
                        cell.setToolTip("")
            if error:
                has_error = True

        self._ok_btn.setEnabled(not has_error)

    def _on_accept(self) -> None:
        new_names = self._compute_new_names()
        self._result_pairs = [
            (path, new_name)
            for path, new_name in zip(self._paths, new_names)
            if path.name != new_name
        ]
        self.accept()

    def result_pairs(self) -> list[tuple[Path, str]]:
        return self._result_pairs


# ── 左右ペイン比較選択 ────────────────────────────────────


class CompareSelectDialog(QDialog):
    ONLY_LEFT = "only_left"
    ONLY_RIGHT = "only_right"
    NEWER = "newer"
    DIFF_SIZE = "diff_size"
    SAME_NAME = "same_name"

    def __init__(self, left_label: str, right_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("比較選択")
        self._mode: str | None = None

        desc = QLabel(
            f"左: {left_label}\n右: {right_label}\n\n"
            "選択するファイルの条件を選んでください。",
            self,
        )
        desc.setWordWrap(True)

        self._rb_only_left = QRadioButton("左にしか無いファイル", self)
        self._rb_only_right = QRadioButton("右にしか無いファイル", self)
        self._rb_newer = QRadioButton("同名で更新日時が新しい方（両ペインで選択）", self)
        self._rb_diff_size = QRadioButton("同名でサイズが違うファイル（両ペインで選択）", self)
        self._rb_same_name = QRadioButton("同名ファイルをすべて選択（両ペインで選択）", self)
        self._rb_only_left.setChecked(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(desc)
        for rb in (
            self._rb_only_left,
            self._rb_only_right,
            self._rb_newer,
            self._rb_diff_size,
            self._rb_same_name,
        ):
            layout.addWidget(rb)
        layout.addStretch()
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        for rb, mode in (
            (self._rb_only_left, self.ONLY_LEFT),
            (self._rb_only_right, self.ONLY_RIGHT),
            (self._rb_newer, self.NEWER),
            (self._rb_diff_size, self.DIFF_SIZE),
            (self._rb_same_name, self.SAME_NAME),
        ):
            if rb.isChecked():
                self._mode = mode
                break
        self.accept()

    def mode(self) -> str | None:
        return self._mode
