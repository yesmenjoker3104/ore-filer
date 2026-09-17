from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QDir, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileSystemModel,
    QLabel,
    QStyledItemDelegate,
    QStyle,
    QTreeView,
    QVBoxLayout,
    QWidget,
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

        if is_current_row and not view.selectionModel().isSelected(index):
            painter.save()
            painter.fillRect(option.rect, QColor(170, 210, 240, 80))
            painter.restore()


class FileTreeView(QTreeView):
    def currentChanged(self, current: QModelIndex, previous: QModelIndex) -> None:
        super().currentChanged(current, previous)

        self._update_row(previous)
        self._update_row(current)

    def _update_row(self, index: QModelIndex) -> None:
        if not index.isValid():
            return

        rect = self.visualRect(index)
        self.viewport().update(0, rect.top(), self.viewport().width(), rect.height())


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

        self.file_view = FileTreeView()
        self.file_view.setModel(self.model)
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
        if record_history and path != self.current_path:
            self._history[:] = [item for item in self._history if item != path]
            self._history.insert(0, path)
            del self._history[200:]

        self.current_path = path
        self.path_label.setText(str(path))

        root_index = self.model.setRootPath(str(path))
        self.file_view.setRootIndex(root_index)
        self.file_view.selectionModel().clearSelection()
        self.path_changed.emit(str(path))

    def history_paths(self) -> list[Path]:
        return list(self._history)

    def go_to_parent(self) -> None:
        parent_path = self.current_path.parent
        if parent_path != self.current_path:
            self.navigate_to(parent_path)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        path = Path(self.model.filePath(index))
        if path.is_dir():
            self.navigate_to(path)

    def reload(self) -> None:
        self.navigate_to(self.current_path)

    def selected_paths(self) -> list[Path]:
        indexes = self.file_view.selectionModel().selectedRows(0)
        return [Path(self.model.filePath(index)) for index in indexes]

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