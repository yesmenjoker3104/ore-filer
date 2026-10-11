from __future__ import annotations

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QStandardItem
from PySide6.QtWidgets import QFileIconProvider

from ore_filer.gui.threads import RemoteListThread
from ore_filer.services.remote_service import RemoteEntry, RemoteError, RemoteSession


class PaneRemoteMixin:
    """リモート（SFTP）フォルダを表示するペインの処理。"""


    def is_remote_view(self) -> bool:
        return self._remote_session is not None


    def remote_session(self) -> RemoteSession | None:
        return self._remote_session


    def open_remote(self, session: RemoteSession, path: str | None = None) -> None:
        self._leave_archive_view()
        self._leave_remote_view()
        self.clear_search_results()
        self.filter_model.set_query("")
        self._remote_session = session
        self._remote_dir = ""
        self.file_view.setModel(self.remote_filter_model)
        self._reconnect_selection_signal()
        self.file_view.setRootIndex(QModelIndex())
        if path is None:
            try:
                path = session.home_dir()
            except RemoteError as error:
                self._leave_remote_view()
                self.remote_error.emit(str(error))
                return
        self._request_remote_listing(path)


    def _leave_remote_view(self) -> None:
        if not self.is_remote_view():
            return
        session = self._remote_session
        self._select_anchor = None
        self._remote_session = None
        self._remote_dir = ""
        self._remote_request_id += 1
        self._remote_focus_name = None
        self.file_view.setRootIndex(QModelIndex())
        self.file_view.setModel(self.filter_model)
        self._reconnect_selection_signal()
        if session is not None:
            session.close()


    def _remote_location(self) -> str:
        session = self._remote_session
        if session is None:
            return ""
        profile = session.profile
        return f"sftp://{profile.username}@{profile.host}:{profile.port}{self._remote_dir}"


    def _request_remote_listing(self, path: str) -> None:
        session = self._remote_session
        if session is None:
            return
        self._remote_request_id += 1
        thread = RemoteListThread(session, path, self._remote_request_id, self)
        thread.listed.connect(self._on_remote_listed)
        thread.failed.connect(self._on_remote_list_failed)
        thread.finished.connect(thread.deleteLater)
        thread.start()


    def _on_remote_listed(self, request_id: int, path: str, entries: list[RemoteEntry]) -> None:
        if request_id != self._remote_request_id or not self.is_remote_view():
            return
        self._remote_dir = path
        self._show_remote_entries(entries)


    def _on_remote_list_failed(self, request_id: int, message: str) -> None:
        if request_id != self._remote_request_id or not self.is_remote_view():
            return
        self._remote_focus_name = None
        self.remote_error.emit(message)


    def _show_remote_entries(self, entries: list[RemoteEntry]) -> None:
        focus_name = self._remote_focus_name
        self._remote_focus_name = None
        self.remote_model.clear()
        self.remote_model.setHorizontalHeaderLabels(["名前"])
        icon_provider = QFileIconProvider()
        folder_icon = icon_provider.icon(QFileIconProvider.IconType.Folder)
        file_icon = icon_provider.icon(QFileIconProvider.IconType.File)
        for entry in entries:
            item = QStandardItem(entry.name)
            item.setIcon(folder_icon if entry.is_dir else file_icon)
            item.setData(entry, Qt.ItemDataRole.UserRole)
            self.remote_model.appendRow(item)

        self.file_view.selectionModel().clearSelection()
        if self.remote_filter_model.rowCount() > 0:
            target = 0
            if focus_name is not None:
                for row, entry in enumerate(entries):
                    if entry.name == focus_name:
                        target = row
                        break
            self.file_view.setCurrentIndex(self.remote_filter_model.index(target, 0))
        self._update_path_label()
        self.path_changed.emit(self._remote_location())


    def selected_remote_entries(self) -> list[RemoteEntry]:
        if not self.is_remote_view():
            return []
        indexes = self.file_view.selectionModel().selectedRows(0)
        return [
            entry
            for index in indexes
            if (entry := self.remote_entry_from_index(index)) is not None
        ]


    def remote_directory(self) -> str:
        return self._remote_dir


    def remote_names(self) -> set[str]:
        names: set[str] = set()
        for row in range(self.remote_model.rowCount()):
            value = self.remote_model.item(row).data(Qt.ItemDataRole.UserRole)
            if isinstance(value, RemoteEntry):
                names.add(value.name)
        return names


    def remote_entry_from_index(self, index: QModelIndex) -> RemoteEntry | None:
        if (
            not self.is_remote_view()
            or not index.isValid()
            or index.model() is not self.remote_filter_model
        ):
            return None
        value = index.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, RemoteEntry) else None


    def enter_remote_directory(self, entry: RemoteEntry) -> None:
        if not self.is_remote_view() or not entry.is_dir:
            return
        self._request_remote_listing(entry.path)


    def reload_remote(self) -> None:
        if self.is_remote_view():
            self._request_remote_listing(self._remote_dir)
