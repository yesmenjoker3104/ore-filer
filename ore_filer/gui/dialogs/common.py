from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
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
