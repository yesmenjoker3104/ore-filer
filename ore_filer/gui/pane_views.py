from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QMimeData,
    QModelIndex,
    QPoint,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QTreeView,
)


def _git_status_color(xy: str) -> QColor | None:
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
    current_changed = Signal()  # カーソル位置が変わったとき emit

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cursor_visible = True
        self._drag_start_pos: QPoint | None = None
        self._drag_start_index: QModelIndex | None = None
        self.setUniformRowHeights(True)
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
        self.current_changed.emit()
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
        self.setDynamicSortFilter(False)
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
