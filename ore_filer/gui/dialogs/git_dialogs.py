from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class GitCommitDialog(QDialog):
    """ステージ済みファイルの一覧とコミットメッセージ入力ダイアログ。"""

    def __init__(self, staged_files: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Git コミット")
        self.resize(640, 400)

        staged_label = QLabel(f"ステージ済み ({len(staged_files)} 件):", self)
        staged_list = QListWidget(self)
        staged_list.addItems(staged_files)
        staged_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        staged_list.setMaximumHeight(150)

        msg_label = QLabel("コミットメッセージ (Ctrl+Enter でコミット):", self)
        self._msg_edit = QPlainTextEdit(self)
        self._msg_edit.setPlaceholderText("コミットメッセージを入力してください")

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_btn.setText("コミット")
        self._ok_btn.setEnabled(False)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(staged_label)
        layout.addWidget(staged_list)
        layout.addWidget(msg_label)
        layout.addWidget(self._msg_edit, 1)
        layout.addWidget(self._buttons)

        self._msg_edit.textChanged.connect(self._on_text_changed)
        self._msg_edit.installEventFilter(self)
        self._msg_edit.setFocus()

    def _on_text_changed(self) -> None:
        self._ok_btn.setEnabled(bool(self._msg_edit.toPlainText().strip()))

    def commit_message(self) -> str:
        return self._msg_edit.toPlainText().strip()

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self._msg_edit
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
        ):
            if self._ok_btn.isEnabled():
                self.accept()
            return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)


class GitLogDialog(QDialog):
    """Git ログ一覧ダイアログ。Enter またはダブルクリックで差分を表示する。"""

    def __init__(self, entries: list[tuple[str, str, str, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Git ログ")
        self.resize(900, 560)
        self._selected_commit: str | None = None

        self._table = QTableWidget(len(entries), 4, self)
        self._table.setHorizontalHeaderLabels(["ハッシュ", "日時", "作者", "メッセージ"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 80)
        self._table.setColumnWidth(1, 140)
        self._table.setColumnWidth(2, 140)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)

        for row, (hash_, date, author, subject) in enumerate(entries):
            self._table.setItem(row, 0, QTableWidgetItem(hash_))
            self._table.setItem(row, 1, QTableWidgetItem(date))
            self._table.setItem(row, 2, QTableWidgetItem(author))
            self._table.setItem(row, 3, QTableWidgetItem(subject))

        if entries:
            self._table.selectRow(0)

        hint = QLabel("Enter / ダブルクリック: 差分表示  |  Esc / Q: 閉じる", self)

        layout = QVBoxLayout(self)
        layout.addWidget(self._table, 1)
        layout.addWidget(hint)

        self._table.doubleClicked.connect(self._accept_selected)
        self._table.setFocus()

    def _accept_selected(self, _index=None) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if item:
            self._selected_commit = item.text()
            self.accept()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept_selected()
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.reject()
        else:
            super().keyPressEvent(event)

    def selected_commit(self) -> str | None:
        return self._selected_commit


class GitHttpCredentialsDialog(QDialog):
    """HTTP/HTTPS リモート操作用の認証情報入力ダイアログ。"""

    def __init__(
        self,
        url: str,
        username: str = "",
        password: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Git 認証")
        self.setMinimumWidth(360)

        self._username_edit = QLineEdit(self)
        self._username_edit.setText(username)
        self._password_edit = QLineEdit(self)
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setText(password)
        self._save_check = QCheckBox("認証情報を保存する（Windows 資格情報マネージャー）", self)
        self._save_check.setChecked(bool(username or password))

        form = QFormLayout()
        form.addRow("ユーザー名:", self._username_edit)
        form.addRow("パスワード / トークン:", self._password_edit)

        url_label = QLabel(f"<small>{url}</small>", self)
        url_label.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(url_label)
        layout.addLayout(form)
        layout.addWidget(self._save_check)
        layout.addWidget(buttons)

        if username:
            self._password_edit.setFocus()
        else:
            self._username_edit.setFocus()

    def credentials(self) -> tuple[str, str]:
        return self._username_edit.text(), self._password_edit.text()

    def should_save(self) -> bool:
        return self._save_check.isChecked()
