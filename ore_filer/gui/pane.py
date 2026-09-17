from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QFileInfo,
    QItemSelectionModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
	QFileIconProvider,
    QFileSystemModel,
    QLabel,
    QStyledItemDelegate,
    QStyle,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ore_filer.services.file_operations import (
    ArchiveEntry,
    list_archive_entries,
)


class FileItemDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        view = self.parent()
        current = view.currentIndex()
        is_current_row = (
            index.row() == current.row() and index.parent() == current.parent()
        )

        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)

        if (
            view.cursor_visible
            and is_current_row
            and not view.selectionModel().isSelected(index)
        ):
            painter.save()
            painter.fillRect(option.rect, QColor(170, 210, 240, 80))
            painter.restore()


class FileTreeView(QTreeView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cursor_visible = True

    def set_cursor_visible(self, visible: bool) -> None:
        if self.cursor_visible == visible:
            return
        self.cursor_visible = visible
        self.viewport().update()

    def currentChanged(self, current: QModelIndex, previous: QModelIndex) -> None:
        super().currentChanged(current, previous)

        self._update_row(previous)
        self._update_row(current)

    def _update_row(self, index: QModelIndex) -> None:
        if not index.isValid():
            return

        rect = self.visualRect(index)
        self.viewport().update(0, rect.top(), self.viewport().width(), rect.height())


class FileFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._query = ""
        self._root_path: Path | None = None
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_root_path(self, path: str | Path) -> None:
        self._root_path = Path(path).expanduser().resolve()

    def set_query(self, query: str) -> None:
        normalized_query = query.casefold()
        if normalized_query == self._query:
            return
        self._query = normalized_query
        self.invalidateFilter()

    def query(self) -> str:
        return self._query

    def has_filter(self) -> bool:
        return bool(self._query)

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QModelIndex,
    ) -> bool:
        if not self._query:
            return True

        source_model = self.sourceModel()
        if source_model is None:
            return False
        index = source_model.index(source_row, 0, source_parent)
        path = Path(source_model.filePath(index))
        if self._root_path is not None and (
            path == self._root_path or path in self._root_path.parents
        ):
            return True
        return self._query in source_model.fileName(index).casefold()


class SearchResultFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._query = ""

    def set_query(self, query: str) -> None:
        normalized_query = query.casefold()
        if normalized_query == self._query:
            return
        self._query = normalized_query
        self.invalidateFilter()

    def has_filter(self) -> bool:
        return bool(self._query)

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QModelIndex,
    ) -> bool:
        if not self._query:
            return True

        source_model = self.sourceModel()
        if source_model is None:
            return False
        index = source_model.index(source_row, 0, source_parent)
        value = index.data(Qt.ItemDataRole.DisplayRole)
        return value is not None and self._query in str(value).casefold()


class PaneWidget(QWidget):
    path_changed = Signal(str)

    def __init__(
        self,
        initial_path: str | Path,
        parent=None,
        history: list[str | Path] | None = None,
    ):
        super().__init__(parent)
        initial_path = Path(initial_path).expanduser()
        self.current_path = (
            initial_path.resolve() if initial_path.is_dir() else Path.home()
        )
        self._history = self._build_history(self.current_path, history)
        self.path_label = QLabel()

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden)
        self.filter_model = FileFilterProxyModel(self)
        self.filter_model.setSourceModel(self.model)
        self.search_model = QStandardItemModel(self)
        self.search_filter_model = SearchResultFilterProxyModel(self)
        self.search_filter_model.setSourceModel(self.search_model)
        self._search_results: list[Path] | None = None
        self.archive_model = QStandardItemModel(self)
        self.archive_filter_model = SearchResultFilterProxyModel(self)
        self.archive_filter_model.setSourceModel(self.archive_model)
        self._archive_path: Path | None = None
        self._archive_member_dir = ""
        self._archive_password: str | None = None

        self.file_view = FileTreeView()
        self.file_view.setModel(self.filter_model)
        self.file_view.setColumnWidth(0, 300)
        self.file_view.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.file_view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.file_view.setItemDelegate(FileItemDelegate(self.file_view))
        self.file_view.setStyleSheet(
            "QTreeView::item:selected { background-color: #3399ff; color: white; }"
        )
        self.file_view.setAlternatingRowColors(True)
        self.file_view.doubleClicked.connect(self._on_double_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(self.path_label)
        layout.addWidget(self.file_view)

        self.navigate_to(self.current_path, record_history=False)

    @staticmethod
    def _build_history(
        initial_path: Path, history: list[str | Path] | None
    ) -> list[Path]:
        paths = [Path(item).expanduser().resolve() for item in (history or [])]
        paths = [path for path in paths if path.is_dir()]
        paths = [path for path in paths if path != initial_path]
        paths.insert(0, initial_path)
        if history is not None:
            history[:] = paths[:200]
            return history
        return paths[:200]

    def navigate_to(self, path: str | Path, *, record_history: bool = True) -> None:
        path = Path(path).resolve()
        if not path.is_dir():
            return
        self._leave_archive_view()
        self.clear_search_results()
        self.clear_filter()
        if record_history and path != self.current_path:
            self._history[:] = [item for item in self._history if item != path]
            self._history.insert(0, path)
            del self._history[200:]

        self.current_path = path
        self.path_label.setText(str(path))

        self.filter_model.set_root_path(path)
        root_index = self.model.setRootPath(str(path))
        self.file_view.setRootIndex(self.filter_model.mapFromSource(root_index))
        self.file_view.selectionModel().clearSelection()
        self.path_changed.emit(str(path))

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
        self._archive_path = None
        self._archive_member_dir = ""
        self._archive_password = None
        self.file_view.setModel(self.filter_model)

    def entry_from_index(self, index: QModelIndex) -> ArchiveEntry | None:
        if (
            not self.is_archive_view()
            or not index.isValid()
            or index.model() is not self.archive_filter_model
        ):
            return None
        value = index.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, ArchiveEntry) else None

    def focused_entry(self) -> ArchiveEntry | None:
        return self.entry_from_index(self.file_view.currentIndex())

    def selected_entries(self) -> list[ArchiveEntry]:
        if not self.is_archive_view():
            return []
        entries: list[ArchiveEntry] = []
        for index in self.file_view.selectionModel().selectedRows(0):
            entry = self.entry_from_index(index)
            if entry is not None:
                entries.append(entry)
        return entries

    def history_paths(self) -> list[Path]:
        return list(self._history)

    def set_filter(self, query: str) -> None:
        if self.is_archive_view():
            current_entry = self.entry_from_index(self.file_view.currentIndex())
            self.archive_filter_model.set_query(query)
            self.file_view.selectionModel().clearSelection()
            if current_entry is not None:
                current_index = self.index_for_entry(current_entry)
                if current_index.isValid():
                    self.file_view.setCurrentIndex(current_index)
                    return
            if self.archive_filter_model.rowCount() > 0:
                self.file_view.setCurrentIndex(
                    self.archive_filter_model.index(0, 0)
                )
            return

        current_path = self.path_from_index(self.file_view.currentIndex())
        self.filter_model.set_query(query)
        self.search_filter_model.set_query(query)
        self.file_view.selectionModel().clearSelection()

        if self._search_results is not None:
            self.file_view.setRootIndex(QModelIndex())
            if current_path is not None:
                current_index = self.index_for_path(current_path)
                if current_index.isValid():
                    self.file_view.setCurrentIndex(current_index)
                    return

            if self.search_filter_model.rowCount() > 0:
                self.file_view.setCurrentIndex(
                    self.search_filter_model.index(0, 0)
                )
            return

        if current_path is not None:
            current_index = self.index_for_path(current_path)
            if current_index.isValid():
                self.file_view.setCurrentIndex(current_index)
                return

        root_index = self.filter_model.mapFromSource(
            self.model.index(str(self.current_path))
        )
        self.file_view.setRootIndex(root_index)
        if self.filter_model.rowCount(root_index) > 0:
            self.file_view.setCurrentIndex(
                self.filter_model.index(0, 0, root_index)
            )

    def clear_filter(self) -> None:
        if self.is_archive_view() and self.archive_filter_model.has_filter():
            self.set_filter("")
        elif self.filter_model.has_filter():
            self.set_filter("")

    def show_search_results(self, paths: list[str | Path]) -> None:
        self._search_results = [Path(path).expanduser().resolve() for path in paths]
        self.search_model.clear()
        self.search_model.setHorizontalHeaderLabels(["名前"])
        icon_provider = QFileIconProvider()
        for path in self._search_results:
            relative_path = path.relative_to(self.current_path)
            item = QStandardItem(str(relative_path))
            item.setIcon(icon_provider.icon(QFileInfo(str(path))))
            item.setData(str(path), Qt.ItemDataRole.UserRole)
            self.search_model.appendRow(item)

        self.search_filter_model.set_query(self.filter_model.query())
        self.file_view.setModel(self.search_filter_model)
        self.file_view.setRootIndex(QModelIndex())
        self.file_view.selectionModel().clearSelection()
        if self.search_filter_model.rowCount() > 0:
            self.file_view.setCurrentIndex(self.search_filter_model.index(0, 0))
        self.path_label.setText(f"検索結果: {self.current_path}")

    def clear_search_results(self) -> None:
        if self._search_results is None:
            return

        self._search_results = None
        self.file_view.setModel(self.filter_model)
        root_index = self.filter_model.mapFromSource(
            self.model.index(str(self.current_path))
        )
        self.file_view.setRootIndex(root_index)
        self.file_view.selectionModel().clearSelection()
        self.path_label.setText(str(self.current_path))

    def has_search_results(self) -> bool:
        return self._search_results is not None

    def has_filter(self) -> bool:
        if self.is_archive_view():
            return self.archive_filter_model.has_filter()
        return self.filter_model.has_filter()

    def filter_text(self) -> str:
        if self.is_archive_view():
            return self.archive_filter_model.query()
        return self.filter_model.query()

    def path_from_index(self, index: QModelIndex) -> Path | None:
        if not index.isValid():
            return None
        if self.is_archive_view():
            return None
        if self._search_results is not None:
            if index.model() is not self.search_filter_model:
                return None
            value = index.data(Qt.ItemDataRole.UserRole)
            return Path(value) if value else None
        if index.model() is not self.filter_model:
            return None
        source_index = self.filter_model.mapToSource(index)
        if not source_index.isValid():
            return None
        return Path(self.model.filePath(source_index))

    def index_for_path(self, path: str | Path) -> QModelIndex:
        if self.is_archive_view():
            return QModelIndex()
        if self._search_results is not None:
            target = Path(path).expanduser().resolve()
            for row in range(self.search_filter_model.rowCount()):
                index = self.search_filter_model.index(row, 0)
                if self.path_from_index(index) == target:
                    return index
            return QModelIndex()

        source_index = self.model.index(str(path))
        if not source_index.isValid():
            return QModelIndex()
        return self.filter_model.mapFromSource(source_index)

    def is_dir(self, index: QModelIndex) -> bool:
        if self.is_archive_view():
            entry = self.entry_from_index(index)
            return entry is not None and entry.is_dir
        path = self.path_from_index(index)
        return path is not None and path.is_dir()

    def index_for_entry(self, entry: ArchiveEntry) -> QModelIndex:
        if not self.is_archive_view():
            return QModelIndex()
        for row in range(self.archive_filter_model.rowCount()):
            index = self.archive_filter_model.index(row, 0)
            if self.entry_from_index(index) == entry:
                return index
        return QModelIndex()

    def reveal_path(self, path: str | Path) -> bool:
        path = Path(path).expanduser().resolve()
        if not path.exists():
            return False

        if path.is_dir():
            self.navigate_to(path)
            return True

        self.navigate_to(path.parent)
        index = self.index_for_path(path)
        if not index.isValid():
            return False
        self.file_view.setCurrentIndex(index)
        self.file_view.scrollTo(
            index,
            QAbstractItemView.ScrollHint.PositionAtCenter,
        )
        return True

    def go_to_parent(self) -> None:
        if self.is_archive_view():
            assert self._archive_path is not None
            if self._archive_member_dir:
                parent_dir = self._archive_member_dir.rpartition("/")[0]
                entries = list_archive_entries(
                    self._archive_path,
                    parent_dir,
                    password=self._archive_password,
                )
                self._archive_member_dir = parent_dir
                self._show_archive_entries(entries)
                return

            archive_path = self._archive_path
            self._leave_archive_view()
            self.navigate_to(archive_path.parent)
            archive_index = self.index_for_path(archive_path)
            if archive_index.isValid():
                self.file_view.setCurrentIndex(archive_index)
            return

        parent_path = self.current_path.parent
        if parent_path != self.current_path:
            self.navigate_to(parent_path)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if self.is_archive_view():
            entry = self.entry_from_index(index)
            if entry is not None and entry.is_dir:
                self.enter_archive_directory(entry)
            return
        path = self.path_from_index(index)
        if path is not None and path.is_dir():
            self.navigate_to(path)

    def reload(self) -> None:
        if self.is_archive_view():
            assert self._archive_path is not None
            entries = list_archive_entries(
                self._archive_path,
                self._archive_member_dir,
                password=self._archive_password,
            )
            self._show_archive_entries(entries)
            return
        self.navigate_to(self.current_path)

    def selected_paths(self) -> list[Path]:
        if self.is_archive_view():
            return []
        indexes = self.file_view.selectionModel().selectedRows(0)
        return [
            path
            for index in indexes
            if (path := self.path_from_index(index)) is not None
        ]

    def toggle_current_selection(self) -> None:
        index = self.file_view.currentIndex()
        if not index.isValid():
            return

        selection_model = self.file_view.selectionModel()
        selection_flag = QItemSelectionModel.SelectionFlag.Rows
        if selection_model.isSelected(index):
            selection_flag |= QItemSelectionModel.SelectionFlag.Deselect
        else:
            selection_flag |= QItemSelectionModel.SelectionFlag.Select

        selection_model.select(index, selection_flag)
        self.file_view.viewport().update()

    def select_current_and_move_down(self) -> None:
        self.toggle_current_selection()

        current_index = self.file_view.currentIndex()
        next_index = self.file_view.indexBelow(current_index)
        if next_index.isValid():
            self.file_view.setCurrentIndex(next_index)