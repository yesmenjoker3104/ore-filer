from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..services.app_launcher import (
    AppLauncherError,
    RegisteredApp,
    SourceFolder,
    load_registry,
    open_with_registered_app,
    scan_application_directory,
    unscan_application_directory,
    update_application,
)

_TITLE = "アプリランチャー"
_SETTINGS_TAB = 1
_HINT_TEXT = "Enter: 起動 / →: アプリ設定 / 文字入力: インクリメンタルフィルタ"
_SEARCH_RESET_MS = 1500
_NAME_COL = 0
_ALIAS_COL = 1
_VISIBLE_COL = 2
_PATH_COL = 3


class _AppList(QListWidget):
    right_pressed = Signal()
    search_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._search = ""
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(self.reset_search)

    def reset_search(self) -> None:
        self._reset_timer.stop()
        if not self._search:
            return
        self._search = ""
        self.search_changed.emit("")

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        key = event.key()
        if key == Qt.Key.Key_Right:
            self.right_pressed.emit()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._search:
            self.reset_search()
            event.accept()
            return
        if key == Qt.Key.Key_Backspace and self._search:
            self._set_search(self._search[:-1])
            event.accept()
            return

        text = event.text()
        modifiers = event.modifiers()
        if text and text.isprintable() and not modifiers & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
        ):
            self._set_search(self._search + text)
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_search(self, text: str) -> None:
        self._search = text
        self._reset_timer.start(_SEARCH_RESET_MS)
        self.search_changed.emit(text)
        if not text:
            return
        row = self._find_row(text, max(self.currentRow(), 0))
        if row >= 0:
            self.setCurrentRow(row)

    def _find_row(self, text: str, start: int) -> int:
        needle = text.casefold()
        count = self.count()
        for offset in range(count):
            row = (start + offset) % count
            app = self.item(row).data(Qt.ItemDataRole.UserRole)
            if needle in app.name.casefold():
                return row
        return -1


class AppLauncherDialog(QDialog):
    """登録済みアプリの一覧（L）と、アプリ登録・編集のタブを持つダイアログ。"""

    def __init__(
        self,
        exe: Path,
        parent: QWidget | None = None,
        *,
        target_files: Sequence[Path] = (),
    ) -> None:
        super().__init__(parent)
        self._exe = exe
        self._target_files = list(target_files)
        self._apps: list[RegisteredApp] = []
        self._sources: list[SourceFolder] = []
        self._loading_table = False

        self.setWindowTitle(_TITLE)
        self.resize(560, 420)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_list_page(), "一覧")
        self._tabs.addTab(self._build_settings_page(), "アプリ設定")

        layout = QVBoxLayout(self)
        layout.addWidget(self._tabs)
        self._reload()

    # ── 画面構築 ─────────────────────────────────────────

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self._list = _AppList()
        self._list.itemActivated.connect(lambda _item: self._launch_selected())
        self._list.right_pressed.connect(lambda: self._tabs.setCurrentIndex(_SETTINGS_TAB))
        self._hint_label = QLabel(_HINT_TEXT)
        self._list.search_changed.connect(self._on_search_changed)
        layout.addWidget(self._hint_label)
        layout.addWidget(self._list)

        buttons = QHBoxLayout()
        launch_button = QPushButton("起動")
        launch_button.clicked.connect(self._launch_selected)
        buttons.addWidget(launch_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        return page

    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        layout.addWidget(QLabel("登録元（フォルダ）"))
        self._source_list = QListWidget()
        self._source_list.currentItemChanged.connect(self._on_source_selected)
        layout.addWidget(self._source_list)

        source_buttons = QHBoxLayout()
        add_folder_button = QPushButton("フォルダを追加")
        add_folder_button.clicked.connect(self._add_folder)
        self._rescan_button = QPushButton("再スキャン")
        self._rescan_button.clicked.connect(self._rescan_selected)
        self._remove_button = QPushButton("削除")
        self._remove_button.clicked.connect(self._remove_selected)
        for button in (add_folder_button, self._rescan_button, self._remove_button):
            source_buttons.addWidget(button)
        layout.addLayout(source_buttons)

        self._recursive_check = QCheckBox("再帰的に検索する（サブフォルダも対象）")
        self._recursive_check.clicked.connect(self._on_recursive_clicked)
        layout.addWidget(self._recursive_check)

        layout.addWidget(QLabel("検知した exe（選択中の登録元）"))
        self._app_table = QTableWidget(0, 4)
        self._app_table.setHorizontalHeaderLabels(["名前", "エイリアス（カンマ区切り）", "一覧に表示", "パス"])
        self._app_table.verticalHeader().setVisible(False)
        self._app_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._app_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._app_table.horizontalHeader().setStretchLastSection(True)
        self._app_table.itemChanged.connect(self._on_app_item_changed)
        layout.addWidget(self._app_table)

        layout.addWidget(QLabel("エイリアスは別名です。変更は即時保存されます。「一覧に表示」を外すと L の一覧から消えます。"))
        return page

    # ── データ操作 ───────────────────────────────────────

    def _reload(self) -> None:
        try:
            self._apps, self._sources = load_registry(self._exe)
        except AppLauncherError as e:
            QMessageBox.warning(self, _TITLE, str(e))
            self._apps, self._sources = [], []
        self._reload_list_tab()
        self._reload_source_list()

    def _reload_list_tab(self) -> None:
        self._list.reset_search()
        self._list.clear()
        for app in self._apps:
            if not app.show_in_list:
                continue
            item = QListWidgetItem(f"{app.name}    {app.path}")
            item.setData(Qt.ItemDataRole.UserRole, app)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def _reload_source_list(self) -> None:
        previous = self._current_source_key()
        self._source_list.clear()
        for source in self._sources:
            scope = "再帰" if source.recursive else "直下"
            item = QListWidgetItem(f"[{scope}]  {source.path}")
            item.setData(Qt.ItemDataRole.UserRole, ("folder", source.path))
            self._source_list.addItem(item)

        restored = False
        for row in range(self._source_list.count()):
            if self._source_list.item(row).data(Qt.ItemDataRole.UserRole) == previous:
                self._source_list.setCurrentRow(row)
                restored = True
                break
        if not restored and self._source_list.count():
            self._source_list.setCurrentRow(0)
        self._refresh_app_table()

    def _current_source_key(self) -> tuple[str, str] | None:
        item = self._source_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _current_source(self) -> SourceFolder | None:
        key = self._current_source_key()
        if key is None:
            return None
        return next((s for s in self._sources if s.path == key[1]), None)

    def _apps_for_source(self, key: tuple[str, str] | None) -> list[RegisteredApp]:
        if key is None:
            return []
        return [app for app in self._apps if app.source == "scanned" and app.source_path == key[1]]

    def _refresh_app_table(self) -> None:
        key = self._current_source_key()
        self._loading_table = True
        try:
            self._app_table.setRowCount(0)
            for app in self._apps_for_source(key):
                row = self._app_table.rowCount()
                self._app_table.insertRow(row)

                name_item = QTableWidgetItem(app.name)
                name_item.setData(Qt.ItemDataRole.UserRole, app)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                alias_item = QTableWidgetItem(", ".join(app.aliases))

                visible_item = QTableWidgetItem()
                visible_item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                visible_item.setCheckState(
                    Qt.CheckState.Checked if app.show_in_list else Qt.CheckState.Unchecked
                )

                path_item = QTableWidgetItem(app.path)
                path_item.setFlags(path_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

                self._app_table.setItem(row, _NAME_COL, name_item)
                self._app_table.setItem(row, _ALIAS_COL, alias_item)
                self._app_table.setItem(row, _VISIBLE_COL, visible_item)
                self._app_table.setItem(row, _PATH_COL, path_item)
        finally:
            self._loading_table = False
        self._rescan_button.setEnabled(key is not None)
        self._remove_button.setEnabled(key is not None)
        source = self._current_source()
        self._recursive_check.setEnabled(source is not None)
        self._recursive_check.setChecked(source is not None and source.recursive)

    def _on_source_selected(self, _current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._refresh_app_table()

    def _on_search_changed(self, text: str) -> None:
        self._hint_label.setText(f"フィルタ: {text}" if text else _HINT_TEXT)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _launch_selected(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        app: RegisteredApp = item.data(Qt.ItemDataRole.UserRole)
        try:
            open_with_registered_app(self._exe, app.name, self._target_files)
        except AppLauncherError as e:
            QMessageBox.warning(self, _TITLE, str(e))
            return
        self.accept()

    def _add_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "フォルダを選択")
        if not directory:
            return
        answer = QMessageBox.question(
            self,
            _TITLE,
            "フォルダ配下を再帰的に探してexeを登録しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        recursive = answer == QMessageBox.StandardButton.Yes
        try:
            result = scan_application_directory(self._exe, Path(directory), recursive=recursive)
        except AppLauncherError as e:
            QMessageBox.warning(self, _TITLE, str(e))
            return
        self._reload()
        self._notify_merged(result)

    def _on_recursive_clicked(self, checked: bool) -> None:
        source = self._current_source()
        if source is None:
            return
        try:
            result = scan_application_directory(self._exe, Path(source.path), recursive=checked)
        except AppLauncherError as e:
            QMessageBox.warning(self, _TITLE, str(e))
            self._recursive_check.setChecked(not checked)
            return
        self._reload()
        self._notify_merged(result)

    def _notify_merged(self, result: dict) -> None:
        messages = []
        covered_by = result.get("covered_by")
        if covered_by:
            messages.append(f"このフォルダは親フォルダ「{covered_by}」に含まれるため、親フォルダに統合しました。")
        absorbed = result.get("absorbed") or []
        if absorbed:
            messages.append("親フォルダに統合したフォルダ（登録は削除、設定は親へ移行）:\n" + "\n".join(absorbed))
        if messages:
            QMessageBox.information(self, _TITLE, "\n\n".join(messages))

    def _rescan_selected(self) -> None:
        source = self._current_source()
        if source is None:
            return
        try:
            scan_application_directory(self._exe, Path(source.path), recursive=source.recursive)
        except AppLauncherError as e:
            QMessageBox.warning(self, _TITLE, str(e))
            return
        self._reload()

    def _remove_selected(self) -> None:
        source = self._current_source()
        if source is None:
            return
        answer = QMessageBox.question(
            self,
            _TITLE,
            f"フォルダ「{source.path}」の登録を解除しますか？\n検知した exe も一覧から削除されます。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            unscan_application_directory(self._exe, Path(source.path))
        except AppLauncherError as e:
            QMessageBox.warning(self, _TITLE, str(e))
            return
        self._reload()

    def _on_app_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_table:
            return
        app: RegisteredApp = self._app_table.item(item.row(), _NAME_COL).data(Qt.ItemDataRole.UserRole)
        try:
            if item.column() == _ALIAS_COL:
                aliases = [alias.strip() for alias in item.text().split(",") if alias.strip()]
                update_application(self._exe, app.path, aliases=aliases)
            elif item.column() == _VISIBLE_COL:
                visible = item.checkState() == Qt.CheckState.Checked
                update_application(self._exe, app.path, show_in_list=visible)
            else:
                return
        except AppLauncherError as e:
            QMessageBox.warning(self, _TITLE, str(e))
        # 表の編集中に行を作り直すと Qt の通知が壊れるため、次のイベントで再読込する
        QTimer.singleShot(0, self._reload)
