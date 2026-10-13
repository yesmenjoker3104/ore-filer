from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


def _fmt_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB ({size:,} バイト)"
    if size < 1024 ** 3:
        return f"{size / (1024 ** 2):.1f} MB ({size:,} バイト)"
    return f"{size / (1024 ** 3):.1f} GB ({size:,} バイト)"


def confirm_list(
    parent,
    title: str,
    message: str,
    items: list[str],
    total_size: int | None = None,
) -> bool:
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)

    label = QLabel(message)
    label.setWordWrap(True)

    list_widget = QListWidget(dialog)
    list_widget.addItems(items)
    list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
    list_widget.setMinimumHeight(120)

    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
    )
    buttons.button(QDialogButtonBox.StandardButton.No).setDefault(True)
    buttons.button(QDialogButtonBox.StandardButton.No).setFocus()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)

    if total_size is not None:
        summary = f"{len(items)}件 / 合計 {_fmt_file_size(total_size)}"
    else:
        summary = f"{len(items)}件"

    layout = QVBoxLayout(dialog)
    layout.addWidget(label)
    layout.addWidget(list_widget, 1)
    layout.addWidget(QLabel(summary))
    layout.addWidget(buttons)

    dialog.resize(520, 420)
    return dialog.exec() == QDialog.DialogCode.Accepted


# ── 衝突解決ダイアログ ─────────────────────────────────────


def _file_info_text(path: Path) -> str:
    """ファイルのサイズと更新日時を短い文字列で返す。"""
    try:
        st = path.stat()
        size = _fmt_file_size(st.st_size)
        mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y/%m/%d %H:%M")
        return f"{size}  /  {mtime}"
    except OSError:
        return "（情報取得失敗）"


class ConflictDialog(QDialog):
    """1ファイル分の上書き確認ダイアログ。

    chosen_action() → "overwrite" | "skip" | "rename" | "newer"
    apply_to_all()  → True なら以降すべてに同じ操作を適用
    exec() → Accepted なら操作を続行、Rejected ならキャンセル（全体中止）
    """

    def __init__(self, src: Path, dst: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("上書き確認")
        self.resize(480, 260)
        self._action = "skip"

        # ── ファイル情報 ──────────────────────────────────────
        info_box = QGroupBox(f"「{dst.name}」はすでに存在します", self)
        form = QFormLayout(info_box)
        form.setContentsMargins(8, 8, 8, 8)
        form.addRow("コピー元:", QLabel(_file_info_text(src)))
        form.addRow("コピー先:", QLabel(_file_info_text(dst)))

        # ── アクションボタン ──────────────────────────────────
        btn_overwrite = QPushButton("上書き(&O)", self)
        btn_skip      = QPushButton("スキップ(&S)", self)
        btn_rename    = QPushButton("別名で保存(&R)", self)
        btn_newer     = QPushButton("新しい方のみ(&N)", self)

        btn_overwrite.clicked.connect(lambda: self._choose("overwrite"))
        btn_skip.clicked.connect(lambda: self._choose("skip"))
        btn_rename.clicked.connect(lambda: self._choose("rename"))
        btn_newer.clicked.connect(lambda: self._choose("newer"))

        btn_row = QHBoxLayout()
        for btn in (btn_overwrite, btn_skip, btn_rename, btn_newer):
            btn_row.addWidget(btn)

        # ── 以降すべてに適用 ─────────────────────────────────
        self._apply_all_cb = QCheckBox("以降すべてに同じ操作を適用する", self)

        # ── キャンセル ───────────────────────────────────────
        cancel_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        cancel_box.rejected.connect(self.reject)

        # ── レイアウト ───────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.addWidget(info_box)
        layout.addLayout(btn_row)
        layout.addWidget(self._apply_all_cb)
        layout.addWidget(cancel_box)

        # デフォルトフォーカスをスキップに
        btn_skip.setDefault(True)
        btn_skip.setFocus()

    def _choose(self, action: str) -> None:
        self._action = action
        self.accept()

    def chosen_action(self) -> str:
        return self._action

    def apply_to_all(self) -> bool:
        return self._apply_all_cb.isChecked()


# ── 複数行テキスト入力ダイアログ ──────────────────────────────


class MultiLineInputDialog(QDialog):
    """複数行テキスト入力ダイアログ。Ctrl+Enter で確定できる。"""

    def __init__(self, title: str, label: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 200)

        self._edit = QPlainTextEdit()
        self._edit.installEventFilter(self)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        layout.addWidget(self._edit, 1)
        layout.addWidget(buttons)

    def text(self) -> str:
        return self._edit.toPlainText()

    def eventFilter(self, watched, event) -> bool:
        if watched is self._edit and event.type() == QEvent.Type.KeyPress:
            if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                    and event.modifiers() == Qt.KeyboardModifier.ControlModifier):
                self.accept()
                return True
        return super().eventFilter(watched, event)
