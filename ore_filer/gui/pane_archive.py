from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import QFileIconProvider

from ore_filer.services.file_operations import ArchiveEntry, list_archive_entries


class PaneArchiveMixin:
    """アーカイブ内部を表示するペインの処理。"""


    def is_archive_view(self) -> bool:
        return self._archive_path is not None


    def archive_path(self) -> Path | None:
        return self._archive_path


    def archive_password(self) -> str | None:
        return self._archive_password


    def open_archive(
        self,
        archive_path: str | Path,
        *,
        password: str | None = None,
    ) -> None:
        archive_path = Path(archive_path).expanduser().resolve()
        entries = list_archive_entries(archive_path, password=password)
        self._search_results = None
        self._archive_path = archive_path
        self._archive_member_dir = ""
        self._archive_password = password
        self.filter_model.set_query("")
        self.archive_filter_model.set_query("")
        self._show_archive_entries(entries)


    def _show_archive_entries(self, entries: list[ArchiveEntry]) -> None:
        self.archive_model.clear()
        self.archive_model.setHorizontalHeaderLabels(["名前"])
        icon_provider = QFileIconProvider()
        folder_icon = icon_provider.icon(QFileIconProvider.IconType.Folder)
        file_icon = icon_provider.icon(QFileIconProvider.IconType.File)
        for entry in entries:
            item = QStandardItem(entry.name)
            item.setIcon(folder_icon if entry.is_dir else file_icon)
            item.setData(entry, Qt.ItemDataRole.UserRole)
            self.archive_model.appendRow(item)

        self.file_view.setModel(self.archive_filter_model)
        self._reconnect_selection_signal()
        self.file_view.setRootIndex(QModelIndex())
        self.file_view.selectionModel().clearSelection()
        if self.archive_filter_model.rowCount() > 0:
            self.file_view.setCurrentIndex(self.archive_filter_model.index(0, 0))

        location = str(self._archive_path)
        if self._archive_member_dir:
            location = f"{location}::{self._archive_member_dir}"
        else:
            location = f"{location}::"
        self.path_label.setText(location)
        self.path_changed.emit(location)


    def enter_archive_directory(self, entry: ArchiveEntry) -> None:
        if not self.is_archive_view() or not entry.is_dir:
            return
        assert self._archive_path is not None
        entries = list_archive_entries(
            self._archive_path,
            entry.member_path,
            password=self._archive_password,
        )
        self._archive_member_dir = entry.member_path
        self._show_archive_entries(entries)


    def _leave_archive_view(self) -> None:
        if not self.is_archive_view():
            return
        self._select_anchor = None
        self._archive_path = None
        self._archive_member_dir = ""
        self._archive_password = None
        self.file_view.setRootIndex(QModelIndex())
        self.file_view.setModel(self.filter_model)
        self._reconnect_selection_signal()
