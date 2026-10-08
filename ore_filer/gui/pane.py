from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QFileInfo,
    QItemSelectionModel,
    QMimeData,
    QModelIndex,
    QPoint,
    QSize,
    QSortFilterProxyModel,
    QTimer,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QPainter, QPen, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    disk_usage,
    list_archive_entries,
)


def _git_status_color(xy: str) -> "QColor | None":
    """git XY コードから色帯の色を返す。None は色帯なし。"""
    if xy == "D~":
        return QColor(230, 140, 40, 180)    # 橙: フォルダに変更あり
    if xy == "??":
        return QColor(128, 128, 128, 180)   # 灰: 未追跡
    if "D" in xy:
        return QColor(220, 50, 50, 180)     # 赤: 削除
    if len(xy) >= 1 and xy[0] == "A":
        return QColor(40, 180, 40, 180)     # 緑: 追加（ステージ済み新規）
    if "M" in xy:
        return QColor(230, 140, 40, 180)    # 橙: 変更
    return None


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 ** 2:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    else:
        return f"{size / 1024 ** 3:.2f} GB"


class FileItemDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        view = self.parent()
        current = view.currentIndex()
        is_current_row = (
            index.row() == current.row() and index.parent() == current.parent()
        )

        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)

        pane = view.parent()
        if hasattr(pane, "bookmarks") and pane.bookmarks:
            path = pane.path_from_index(index)
            if path and str(path).casefold() in pane.bookmarks:
                painter.save()
                painter.fillRect(option.rect, QColor(150, 110, 20, 90))
                painter.restore()

        # Git 状態の色帯（列0のみ、通常ディレクトリモード）
        if index.column() == 0 and hasattr(pane, "git_status") and pane.git_status:
            path = pane.path_from_index(index)
            if path is not None:
                xy = pane.git_status.get(str(path).casefold())
                if xy:
                    color = _git_status_color(xy)
                    if color is not None:
                        painter.save()
                        band = option.rect.adjusted(0, 0, -(option.rect.width() - 4), 0)
                        painter.fillRect(band, color)
                        painter.restore()

        if (
            view.cursor_visible
            and is_current_row
            and not view.selectionModel().isSelected(index)
        ):
            painter.save()
            painter.fillRect(option.rect, QColor(170, 210, 240, 80))
            painter.restore()
        elif view.cursor_visible and is_current_row:
            painter.save()
            painter.fillRect(option.rect, QColor(0, 40, 110, 110))
            painter.setPen(QPen(QColor(255, 200, 0), 2))
            rect = option.rect.adjusted(0, 1, 0, -1)
            painter.drawLine(rect.topLeft(), rect.topRight())
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.restore()


class FileTreeView(QTreeView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cursor_visible = True
        self._drag_start_pos: QPoint | None = None
        self._drag_start_index: QModelIndex | None = None
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.pos()
            self._drag_start_index = self.indexAt(event.pos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
            and (event.pos() - self._drag_start_pos).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._start_drag()
            return
        super().mouseMoveEvent(event)

    def _start_drag(self) -> None:
        self._drag_start_pos = None
        pane = self.parent()
        if not hasattr(pane, "selected_paths"):
            return
        if pane.is_archive_view():
            return

        paths = pane.selected_paths()
        if not paths and self._drag_start_index is not None and self._drag_start_index.isValid():
            path = pane.path_from_index(self._drag_start_index)
            if path is not None:
                paths = [path]

        if not paths:
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])

        drag = QDrag(self)
        drag.setMimeData(mime)
        result = drag.exec(
            Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
            Qt.DropAction.CopyAction,
        )

        # 受け側が MoveAction を返した（外部アプリが移動した）場合はリロード
        if result == Qt.DropAction.MoveAction:
            pane.reload()

    def dragEnterEvent(self, event) -> None:
        pane = self.parent()
        if hasattr(pane, "is_archive_view") and (
            pane.is_archive_view() or pane.has_search_results()
        ):
            event.ignore()
            return
        if event.mimeData().hasUrls() and any(
            u.isLocalFile() for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        pane = self.parent()
        if hasattr(pane, "is_archive_view") and (
            pane.is_archive_view() or pane.has_search_results()
        ):
            event.ignore()
            return
        if event.mimeData().hasUrls() and any(
            u.isLocalFile() for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        pane = self.parent()
        if not hasattr(pane, "files_dropped"):
            event.ignore()
            return
        if pane.is_archive_view() or pane.has_search_results():
            event.ignore()
            return

        urls = event.mimeData().urls()
        paths = [url.toLocalFile() for url in urls if url.isLocalFile()]
        if not paths:
            event.ignore()
            return

        # ドロップ先: カーソル下がフォルダならそのフォルダ、それ以外は current_path
        index = self.indexAt(event.pos())
        destination = str(pane.current_path)
        if index.isValid():
            p = pane.path_from_index(index)
            if p is not None and p.is_dir():
                destination = str(p)

        is_move = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # 常に CopyAction として受け入れる（エクスプローラーに元ファイルを消させない）
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

        pane.files_dropped.emit(paths, destination, is_move)

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


class ElidedLabel(QLabel):
    """幅に収まらない場合に中央を「...」で省略するラベル。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self.update()

    def minimumSizeHint(self) -> QSize:
        h = super().minimumSizeHint().height()
        return QSize(0, h)

    def sizeHint(self) -> QSize:
        h = super().sizeHint().height()
        return QSize(0, h)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        fm = painter.fontMetrics()
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, self.width())
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )


SORT_NAME = "name"
SORT_EXT = "ext"
SORT_SIZE = "size"
SORT_DATE = "date"


class FileFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._query = ""
        self._root_path: Path | None = None
        self._sort_mode = SORT_NAME
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def set_root_path(self, path: str | Path) -> None:
        self._root_path = Path(path).expanduser().resolve()

    def set_sort_mode(self, mode: str) -> None:
        self._sort_mode = mode
        self.invalidate()

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        source = self.sourceModel()
        left_is_dir = source.isDir(left)
        right_is_dir = source.isDir(right)
        if left_is_dir != right_is_dir:
            ascending = self.sortOrder() == Qt.SortOrder.AscendingOrder
            return left_is_dir if ascending else not left_is_dir

        if self._sort_mode == SORT_SIZE:
            return source.size(left) < source.size(right)
        if self._sort_mode == SORT_DATE:
            return source.lastModified(left) < source.lastModified(right)
        if self._sort_mode == SORT_EXT:
            left_ext = Path(source.fileName(left)).suffix.casefold()
            right_ext = Path(source.fileName(right)).suffix.casefold()
            if left_ext != right_ext:
                return left_ext < right_ext
            return source.fileName(left).casefold() < source.fileName(right).casefold()
        return source.fileName(left).casefold() < source.fileName(right).casefold()

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
        self._sort_mode = SORT_NAME

    def set_query(self, query: str) -> None:
        normalized_query = query.casefold()
        if normalized_query == self._query:
            return
        self._query = normalized_query
        self.invalidateFilter()

    def set_sort_mode(self, mode: str) -> None:
        self._sort_mode = mode
        self.invalidate()

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

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        left_path = Path(left.data(Qt.ItemDataRole.UserRole) or "")
        right_path = Path(right.data(Qt.ItemDataRole.UserRole) or "")
        left_is_dir = left_path.is_dir()
        right_is_dir = right_path.is_dir()
        if left_is_dir != right_is_dir:
            ascending = self.sortOrder() == Qt.SortOrder.AscendingOrder
            return left_is_dir if ascending else not left_is_dir

        if self._sort_mode == SORT_SIZE:
            try:
                return left_path.stat().st_size < right_path.stat().st_size
            except OSError:
                pass
        elif self._sort_mode == SORT_DATE:
            try:
                return left_path.stat().st_mtime < right_path.stat().st_mtime
            except OSError:
                pass
        elif self._sort_mode == SORT_EXT:
            left_ext = left_path.suffix.casefold()
            right_ext = right_path.suffix.casefold()
            if left_ext != right_ext:
                return left_ext < right_ext
            return left_path.name.casefold() < right_path.name.casefold()
        return left_path.name.casefold() < right_path.name.casefold()


class PaneWidget(QWidget):
    path_changed = Signal(str)
    selection_changed = Signal()
    files_dropped = Signal(list, str, bool)  # (paths, destination, is_move)

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
        self.path_label = ElidedLabel()

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.Hidden)
        self.model.directoryLoaded.connect(self._on_directory_loaded)
        self.filter_model = FileFilterProxyModel(self)
        self.filter_model.setSourceModel(self.model)
        self.search_model = QStandardItemModel(self)
        self.search_filter_model = SearchResultFilterProxyModel(self)
        self.search_filter_model.setSourceModel(self.search_model)
        self._search_results: list[Path] | None = None
        self._grep_hits: dict[Path, list[int]] = {}
        self.archive_model = QStandardItemModel(self)
        self.archive_filter_model = SearchResultFilterProxyModel(self)
        self.archive_filter_model.setSourceModel(self.archive_model)
        self._archive_path: Path | None = None
        self._archive_member_dir = ""
        self._archive_password: str | None = None
        self._sort_mode = SORT_NAME
        self._sort_order = Qt.SortOrder.AscendingOrder
        self.bookmarks: set[str] = set()
        self._select_anchor: int | None = None
        self._cursor_memory: dict[Path, str] = {}
        self._pending_focus: str | None = None

        # Git 状態（casefolded パス文字列 → XY コード）
        self.git_status: dict[str, str] = {}
        self.git_branch: str | None = None

        self._font_size: int = self.font().pointSize()
        if self._font_size <= 0:
            self._font_size = 10

        self.file_view = FileTreeView()
        self.file_view.setModel(self.filter_model)
        self._reconnect_selection_signal()
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

    def navigate_to(
        self,
        path: str | Path,
        *,
        record_history: bool = True,
        focus_name: str | None = None,
    ) -> None:
        path = Path(path).resolve()
        if not path.is_dir():
            return
        self._select_anchor = None

        # 移動前のカーソル位置を記憶
        if not self.is_archive_view() and self._search_results is None:
            idx = self.file_view.currentIndex()
            cur = self.path_from_index(idx)
            if cur is not None:
                self._cursor_memory[self.current_path] = cur.name

        self._leave_archive_view()
        self.clear_search_results()
        self.clear_filter()
        if record_history and path != self.current_path:
            self._history[:] = [item for item in self._history if item != path]
            self._history.insert(0, path)
            del self._history[200:]

        self.current_path = path
        self._update_path_label()

        # フォーカス目標: 明示指定 > 記憶 > 先頭（""で先頭を表す）
        self._pending_focus = focus_name or self._cursor_memory.get(path) or ""

        # root index をクリアしてから root path を変更することで、
        # QFileSystemModel の再構築中に古い persistent index が
        # mapToSource に渡るのを防ぐ
        self.file_view.setRootIndex(QModelIndex())
        self.filter_model.set_root_path(path)
        root_index = self.model.setRootPath(str(path))
        proxy_root = self.filter_model.mapFromSource(root_index)
        if proxy_root.isValid():
            self.file_view.setRootIndex(proxy_root)
        self.filter_model.sort(0, self._sort_order)
        self.file_view.selectionModel().clearSelection()
        self.path_changed.emit(str(path))

        self._apply_pending_focus()

    def focus_name(self, name: str) -> None:
        self._pending_focus = name
        self._apply_pending_focus()
        if self._pending_focus is not None:
            QTimer.singleShot(300, self._apply_pending_focus)

    def _update_path_label(self) -> None:
        labels = {SORT_NAME: "名前", SORT_EXT: "拡張子", SORT_SIZE: "サイズ", SORT_DATE: "日時"}
        arrow = "↑" if self._sort_order == Qt.SortOrder.AscendingOrder else "↓"
        prefix = f"[{labels[self._sort_mode]}{arrow}]"
        if self._search_results is not None:
            self.path_label.setText(f"{prefix} 検索結果: {self.current_path}")
        else:
            usage = disk_usage(self.current_path)
            if usage is not None:
                free, total = usage
                disk_info = f"  （空き {_fmt_size(free)} / {_fmt_size(total)}）"
            else:
                disk_info = ""
            branch_info = f"  [git: {self.git_branch}]" if self.git_branch else ""
            self.path_label.setText(f"{prefix} {self.current_path}{disk_info}{branch_info}")

    def set_git_info(self, branch: str | None, status_map: dict[str, str]) -> None:
        """Git の状態情報を更新してパス欄とリストを再描画する。"""
        self.git_status = status_map
        self.git_branch = branch
        self._update_path_label()
        self.file_view.viewport().update()

    def set_sort(self, mode: str, order: Qt.SortOrder) -> None:
        self._sort_mode = mode
        self._sort_order = order
        self.filter_model.set_sort_mode(mode)
        self.filter_model.sort(0, order)
        if self._search_results is not None:
            self.search_filter_model.set_sort_mode(mode)
            self.search_filter_model.sort(0, order)
        self._update_path_label()

    def sort_mode(self) -> str:
        return self._sort_mode

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

    def show_search_results(
        self,
        paths: list[str | Path],
        line_hits: dict[Path, list[int]] | None = None,
    ) -> None:
        self._grep_hits = line_hits or {}
        self._search_results = [Path(path).expanduser().resolve() for path in paths]
        self.search_model.clear()
        self.search_model.setHorizontalHeaderLabels(["名前", "サイズ", "更新日時"])
        icon_provider = QFileIconProvider()
        for path in self._search_results:
            try:
                relative_path = path.relative_to(self.current_path)
            except ValueError:
                relative_path = path
            fi = QFileInfo(str(path))
            # 列0: 名前 + アイコン + フルパス
            name_item = QStandardItem(str(relative_path))
            name_item.setIcon(icon_provider.icon(fi))
            name_item.setData(str(path), Qt.ItemDataRole.UserRole)
            # 列1: サイズ（フォルダは空欄）
            if fi.isDir():
                size_text = ""
            else:
                size_text = _fmt_size(fi.size())
            size_item = QStandardItem(size_text)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # 列2: 更新日時
            dt = fi.lastModified()
            date_item = QStandardItem(dt.toString("yyyy-MM-dd HH:mm"))
            self.search_model.appendRow([name_item, size_item, date_item])

        self.search_filter_model.set_query(self.filter_model.query())
        self.search_filter_model.set_sort_mode(self._sort_mode)
        self.file_view.setModel(self.search_filter_model)
        self._reconnect_selection_signal()
        self.file_view.setRootIndex(QModelIndex())
        self.search_filter_model.sort(0, self._sort_order)
        self.file_view.selectionModel().clearSelection()
        header = self.file_view.header()
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        header.setSectionResizeMode(1, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.ResizeToContents)
        if self.search_filter_model.rowCount() > 0:
            self.file_view.setCurrentIndex(self.search_filter_model.index(0, 0))
        self._update_path_label()

    def clear_search_results(self) -> None:
        if self._search_results is None:
            return

        self._select_anchor = None
        self._search_results = None
        self._grep_hits = {}
        self.file_view.setRootIndex(QModelIndex())
        self.file_view.setModel(self.filter_model)
        self._reconnect_selection_signal()
        header = self.file_view.header()
        header.setSectionResizeMode(0, header.ResizeMode.Stretch)
        root_index = self.filter_model.mapFromSource(
            self.model.index(str(self.current_path))
        )
        if root_index.isValid():
            self.file_view.setRootIndex(root_index)
        self.file_view.selectionModel().clearSelection()
        self._update_path_label()

    def has_search_results(self) -> bool:
        return self._search_results is not None

    def grep_lines(self, path: Path) -> list[int]:
        """GREP結果の該当行番号リストを返す（なければ空リスト）。"""
        return self._grep_hits.get(Path(path).resolve(), [])

    def set_show_hidden(self, show: bool) -> None:
        """隠しファイルの表示/非表示を切り替える。"""
        flags = QDir.AllEntries | QDir.NoDotAndDotDot
        if show:
            flags |= QDir.Hidden
        self.model.setFilter(flags)

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
            # UserRole は列0にのみ設定されているため列0に正規化する
            col0 = index.sibling(index.row(), 0)
            value = col0.data(Qt.ItemDataRole.UserRole)
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
            self.navigate_to(archive_path.parent, focus_name=archive_path.name)
            return

        parent_path = self.current_path.parent
        if parent_path != self.current_path:
            self.navigate_to(parent_path, focus_name=self.current_path.name)

    def go_to_first_item(self) -> None:
        root = self.file_view.rootIndex()
        first = self.file_view.model().index(0, 0, root)
        if first.isValid():
            self.file_view.setCurrentIndex(first)
            self.file_view.scrollTo(first)

    def go_to_last_item(self) -> None:
        root = self.file_view.rootIndex()
        count = self.file_view.model().rowCount(root)
        if count > 0:
            last = self.file_view.model().index(count - 1, 0, root)
            if last.isValid():
                self.file_view.setCurrentIndex(last)
                self.file_view.scrollTo(last)

    def move_page_up(self) -> None:
        """カーソルを1ページ分上へ移動する。"""
        self._move_page(QAbstractItemView.CursorAction.MovePageUp)

    def move_page_down(self) -> None:
        """カーソルを1ページ分下へ移動する。"""
        self._move_page(QAbstractItemView.CursorAction.MovePageDown)

    def _move_page(self, action: QAbstractItemView.CursorAction) -> None:
        index = self.file_view.moveCursor(
            action,
            Qt.KeyboardModifier.NoModifier,
        )
        if index.isValid():
            self.file_view.setCurrentIndex(index)

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

    def deselect_all(self) -> None:
        self.file_view.selectionModel().clearSelection()
        self.file_view.viewport().update()

    def _iter_selectable_indexes(self, include_dirs: bool) -> list[QModelIndex]:
        model = self.file_view.model()
        if model is None:
            return []
        root = self.file_view.rootIndex()
        result = []
        for row in range(model.rowCount(root)):
            index = model.index(row, 0, root)
            path = self.path_from_index(index)
            if path is None or path.name == "..":
                continue
            if not include_dirs and path.is_dir():
                continue
            result.append(index)
        return result

    def select_all_files(self) -> None:
        """ファイルのみ全選択。全選択済みの場合は選択解除（トグル）。"""
        indexes = self._iter_selectable_indexes(include_dirs=False)
        if not indexes:
            return
        sel = self.file_view.selectionModel()
        all_selected = all(sel.isSelected(i) for i in indexes)
        if all_selected:
            sel.clearSelection()
        else:
            for i in indexes:
                sel.select(i, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.file_view.viewport().update()

    def select_all_items(self) -> None:
        """ファイル＋ディレクトリを全選択。全選択済みの場合は選択解除（トグル）。"""
        indexes = self._iter_selectable_indexes(include_dirs=True)
        if not indexes:
            return
        sel = self.file_view.selectionModel()
        all_selected = all(sel.isSelected(i) for i in indexes)
        if all_selected:
            sel.clearSelection()
        else:
            for i in indexes:
                sel.select(i, QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
        self.file_view.viewport().update()

    def select_current_and_move_down(self) -> None:
        self.toggle_current_selection()

        current_index = self.file_view.currentIndex()
        next_index = self.file_view.indexBelow(current_index)
        if next_index.isValid():
            self.file_view.setCurrentIndex(next_index)

    def select_current_and_move_up(self) -> None:
        self.toggle_current_selection()
        current_index = self.file_view.currentIndex()
        prev_index = self.file_view.indexAbove(current_index)
        if prev_index.isValid():
            self.file_view.setCurrentIndex(prev_index)

    def range_select(self) -> None:
        """Ctrl+Space: アンカー設定、または アンカー〜カーソル間を範囲選択。"""
        index = self.file_view.currentIndex()
        if not index.isValid():
            return
        current_row = index.row()
        if self._select_anchor is None:
            self._select_anchor = current_row
            self.file_view.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )
        else:
            anchor_row = self._select_anchor
            self._select_anchor = None
            model = self.file_view.model()
            root = self.file_view.rootIndex()
            if model is None:
                return
            lo, hi = min(anchor_row, current_row), max(anchor_row, current_row)
            sel = self.file_view.selectionModel()
            for row in range(lo, hi + 1):
                idx = model.index(row, 0, root)
                if idx.isValid():
                    path = self.path_from_index(idx)
                    if path is None or path.name != "..":
                        sel.select(
                            idx,
                            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                        )
        self.file_view.viewport().update()

    def _apply_pending_focus(self) -> None:
        if self._pending_focus is None:
            return
        root = self.file_view.rootIndex()
        model = self.file_view.model()
        if model is None or model.rowCount(root) == 0:
            return  # directoryLoaded で再試行

        target = self._pending_focus
        self._pending_focus = None

        if target:
            target_lower = target.casefold()
            for row in range(model.rowCount(root)):
                index = model.index(row, 0, root)
                path = self.path_from_index(index)
                if path and path.name.casefold() == target_lower:
                    self.file_view.setCurrentIndex(index)
                    self.file_view.scrollTo(index)
                    return

        first = model.index(0, 0, root)
        if first.isValid():
            self.file_view.setCurrentIndex(first)
            self.file_view.scrollTo(first)

    def _on_directory_loaded(self, loaded_path: str) -> None:
        if self._pending_focus is None:
            return
        try:
            if Path(loaded_path).resolve() == self.current_path:
                self._apply_pending_focus()
        except Exception:
            pass

    def _reconnect_selection_signal(self) -> None:
        sm = self.file_view.selectionModel()
        if sm is None:
            return
        if getattr(self, "_sel_sm_connected", None) is sm:
            try:
                sm.selectionChanged.disconnect(self.selection_changed)
            except (TypeError, RuntimeError):
                pass
        sm.selectionChanged.connect(self.selection_changed)
        self._sel_sm_connected = sm

    def adjust_font_size(self, delta: int) -> None:
        """フォントとアイコンサイズを delta pt 変更する（8〜32pt の範囲）。"""
        new_size = max(8, min(32, self._font_size + delta))
        if new_size == self._font_size:
            return
        self._font_size = new_size
        font = self.file_view.font()
        font.setPointSize(new_size)
        self.file_view.setFont(font)
        icon_size = max(12, new_size + 4)
        self.file_view.setIconSize(QSize(icon_size, icon_size))
        # 行の高さを font に合わせて再計算させる
        self.file_view.scheduleDelayedItemsLayout()

    def visible_item_count(self) -> int:
        model = self.file_view.model()
        if model is None:
            return 0
        return model.rowCount(self.file_view.rootIndex())

    def selected_count(self) -> int:
        return len(self.file_view.selectionModel().selectedRows(0))

    def selected_total_size(self) -> int:
        total = 0
        for path in self.selected_paths():
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        return total

    def select_paths(self, paths: list[Path]) -> None:
        """指定パスに一致する行を選択に追加する（名前は casefold で比較）。"""
        targets = {p.name.casefold() for p in paths}
        sel = self.file_view.selectionModel()
        for index in self._iter_selectable_indexes(include_dirs=True):
            path = self.path_from_index(index)
            if path and path.name.casefold() in targets:
                sel.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
        self.file_view.viewport().update()

    def select_by_pattern(self, patterns: list[str]) -> int:
        """ワイルドカードパターンに一致する行を選択し、選択件数を返す。"""
        import fnmatch
        count = 0
        sel = self.file_view.selectionModel()
        for index in self._iter_selectable_indexes(include_dirs=True):
            path = self.path_from_index(index)
            if path is None:
                continue
            name_lower = path.name.casefold()
            if any(fnmatch.fnmatch(name_lower, pat.strip().casefold()) for pat in patterns):
                sel.select(
                    index,
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )
                count += 1
        self.file_view.viewport().update()
        return count