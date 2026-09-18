from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
	QDialog,
	QDialogButtonBox,
	QFormLayout,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QListWidget,
	QListWidgetItem,
	QPushButton,
	QVBoxLayout,
	QWidget,
)

from ore_filer.services.search_service import find_paths


class SearchThread(QThread):
	results_ready = Signal(list)
	error = Signal(str)

	def __init__(self, root: Path, query: str, parent=None):
		super().__init__(parent)
		self.root = root
		self.query = query

	def run(self) -> None:
		try:
			results = find_paths(
				self.root,
				self.query,
				is_cancelled=self.isInterruptionRequested,
			)
		except OSError as error:
			self.error.emit(str(error))
		else:
			self.results_ready.emit(results)


class SearchDialog(QDialog):
	def __init__(self, root: str | Path, parent=None):
		super().__init__(parent)
		self.root = Path(root).expanduser().resolve()
		self._thread: SearchThread | None = None
		self._selected_path: Path | None = None
		self._results: list[Path] = []
		self._closing = False

		self.setWindowTitle("ファイル検索")
		self.resize(760, 520)

		self.query_edit = QLineEdit(self)
		self.query_edit.setPlaceholderText(
			"ファイル名またはフォルダー名（* / ? 使用可）"
		)
		self.search_button = QPushButton("検索", self)
		self.search_button.clicked.connect(self._start_search)
		self.query_edit.returnPressed.connect(self._start_search)

		search_layout = QHBoxLayout()
		search_layout.addWidget(self.query_edit)
		search_layout.addWidget(self.search_button)

		self.status_label = QLabel(f"検索場所: {self.root}", self)
		self.result_list = QListWidget(self)
		self.result_list.setAlternatingRowColors(True)
		self.result_list.setTextElideMode(Qt.TextElideMode.ElideNone)
		self.result_list.setHorizontalScrollBarPolicy(
			Qt.ScrollBarPolicy.ScrollBarAsNeeded
		)
		self.result_list.itemDoubleClicked.connect(self._accept_selected)

		self.buttons = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Ok
			| QDialogButtonBox.StandardButton.Cancel,
			parent=self,
		)
		self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
		self.ok_button.setEnabled(False)
		self.buttons.accepted.connect(self._accept_selected)
		self.buttons.rejected.connect(self.reject)

		layout = QVBoxLayout(self)
		layout.addLayout(search_layout)
		layout.addWidget(self.status_label)
		layout.addWidget(self.result_list)
		layout.addWidget(self.buttons)

	def _start_search(self) -> None:
		if self._thread is not None and self._thread.isRunning():
			return

		query = self.query_edit.text().strip()
		if not query:
			self.status_label.setText("検索文字列を入力してください")
			return

		self._selected_path = None
		self._results = []
		self.result_list.clear()
		self.ok_button.setEnabled(False)
		self.query_edit.setEnabled(False)
		self.search_button.setEnabled(False)
		self.status_label.setText("検索中...")

		thread = SearchThread(self.root, query, self)
		thread.results_ready.connect(self._show_results)
		thread.error.connect(self._show_error)
		thread.finished.connect(self._search_finished)
		self._thread = thread
		thread.start()

	def _show_results(self, paths: list[Path]) -> None:
		if self._closing:
			return

		self._results = list(paths)
		for path in paths:
			relative_path = path.relative_to(self.root)
			item = QListWidgetItem(str(relative_path))
			item.setData(Qt.ItemDataRole.UserRole, str(path))
			self.result_list.addItem(item)

		if paths:
			self.result_list.setCurrentRow(0)
			self.ok_button.setEnabled(True)
			self.status_label.setText(f"{len(paths)}件見つかりました")
		else:
			self.status_label.setText("見つかりませんでした")

	def _show_error(self, message: str) -> None:
		if not self._closing:
			self.status_label.setText(f"検索に失敗しました: {message}")

	def _search_finished(self) -> None:
		thread = self._thread
		self._thread = None
		if thread is not None:
			thread.deleteLater()

		if self._closing:
			self.done(QDialog.DialogCode.Rejected)
			return

		self.query_edit.setEnabled(True)
		self.search_button.setEnabled(True)

	def _accept_selected(self, _item: QListWidgetItem | None = None) -> None:
		item = self.result_list.currentItem()
		if item is None:
			return

		value = item.data(Qt.ItemDataRole.UserRole)
		if value:
			self._selected_path = Path(value)
			super().accept()

	def selected_path(self) -> Path | None:
		return self._selected_path

	def result_paths(self) -> list[Path]:
		return list(self._results)

	def reject(self) -> None:
		if self._thread is not None and self._thread.isRunning():
			self._closing = True
			self.status_label.setText("検索を中止しています...")
			self.query_edit.setEnabled(False)
			self.search_button.setEnabled(False)
			self.ok_button.setEnabled(False)
			self._thread.requestInterruption()
			return
		super().reject()

	def closeEvent(self, event) -> None:
		if self._thread is not None and self._thread.isRunning():
			self.reject()
			event.ignore()
			return
		super().closeEvent(event)


class HistoryDialog(QDialog):
	def __init__(self, history: list[str | Path], parent=None):
		super().__init__(parent)
		self.setWindowTitle("履歴")
		self.resize(760, 520)
		self._history = [Path(path) for path in history]

		self.filter_edit = QLineEdit(self)
		self.filter_edit.setPlaceholderText("履歴を絞り込む（F）")
		self.filter_edit.textChanged.connect(self._apply_filter)

		self.list_widget = QListWidget(self)
		self.list_widget.setAlternatingRowColors(True)
		self.list_widget.setTextElideMode(Qt.TextElideMode.ElideNone)
		self.list_widget.setHorizontalScrollBarPolicy(
			Qt.ScrollBarPolicy.ScrollBarAsNeeded
		)
		self.list_widget.installEventFilter(self)
		self.list_widget.itemDoubleClicked.connect(
			lambda _item: self._accept_selected()
		)

		self.buttons = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Ok
			| QDialogButtonBox.StandardButton.Cancel,
			parent=self,
		)
		self.buttons.accepted.connect(self._accept_selected)
		self.buttons.rejected.connect(self.reject)

		layout = QVBoxLayout(self)
		layout.addWidget(self.filter_edit)
		layout.addWidget(self.list_widget)
		layout.addWidget(self.buttons)
		self._apply_filter("")
		self.list_widget.setFocus()

	def _apply_filter(self, query: str) -> None:
		query = query.strip().casefold()
		self.list_widget.clear()
		for path in self._history:
			if query and query not in str(path).casefold():
				continue
			item = QListWidgetItem(str(path))
			item.setData(Qt.ItemDataRole.UserRole, str(path))
			self.list_widget.addItem(item)

		if self.list_widget.count() > 0:
			self.list_widget.setCurrentRow(0)

		self.list_widget.setMinimumWidth(
			self.list_widget.sizeHintForColumn(0)
			+ (self.list_widget.frameWidth() * 2)
			+ self.list_widget.verticalScrollBar().sizeHint().width()
		)

	def _accept_selected(self) -> None:
		if self.list_widget.currentItem() is None:
			return
		self.accept()

	def selected_path(self) -> Path | None:
		item = self.list_widget.currentItem()
		if item is None:
			return None
		value = item.data(Qt.ItemDataRole.UserRole)
		return Path(value) if value else None

	def eventFilter(self, watched, event) -> bool:
		if watched is self.list_widget and event.type() == QEvent.Type.KeyPress:
			if (
				event.key() == Qt.Key.Key_F
				and event.modifiers() == Qt.KeyboardModifier.NoModifier
			):
				self.filter_edit.setFocus()
				self.filter_edit.selectAll()
				return True
		return super().eventFilter(watched, event)


_SORT_OPTIONS = [
    ("name", "ファイル名"),
    ("ext", "拡張子"),
    ("size", "サイズ"),
    ("date", "更新日時"),
]


class SortDialog(QDialog):
    def __init__(self, current_mode: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ソート")
        self._result_mode: str | None = None
        self._result_order: Qt.SortOrder | None = None

        self.list_widget = QListWidget(self)
        initial_row = 0
        for i, (mode, label) in enumerate(_SORT_OPTIONS):
            self.list_widget.addItem(label)
            if mode == current_mode:
                initial_row = i
        self.list_widget.setCurrentRow(initial_row)
        self.list_widget.itemDoubleClicked.connect(self._accept_ascending)

        hint = QLabel("Shift+Enter: 降順")

        layout = QVBoxLayout(self)
        layout.addWidget(self.list_widget)
        layout.addWidget(hint)

        self.list_widget.setFocus()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._finish(Qt.SortOrder.DescendingOrder)
            else:
                self._finish(Qt.SortOrder.AscendingOrder)
        elif key == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)

    def _accept_ascending(self, _item) -> None:
        self._finish(Qt.SortOrder.AscendingOrder)

    def _finish(self, order: Qt.SortOrder) -> None:
        row = self.list_widget.currentRow()
        if 0 <= row < len(_SORT_OPTIONS):
            self._result_mode = _SORT_OPTIONS[row][0]
            self._result_order = order
            self.accept()

    def result_mode(self) -> str | None:
        return self._result_mode

    def result_order(self) -> "Qt.SortOrder | None":
        return self._result_order


class FilterDialog(QDialog):
	filter_changed = Signal(str)

	def __init__(self, initial_text: str = "", parent=None):
		super().__init__(parent)
		self.setWindowTitle("インクリメンタルフィルター")
		self.resize(420, 110)

		self.query_edit = QLineEdit(self)
		self.query_edit.setPlaceholderText("表示する名前に含まれる文字")
		self.query_edit.textChanged.connect(self.filter_changed)
		self.query_edit.setText(initial_text)
		self.query_edit.selectAll()
		self.query_edit.setFocus()

		buttons = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Ok
			| QDialogButtonBox.StandardButton.Cancel,
			parent=self,
		)
		buttons.accepted.connect(self.accept)
		buttons.rejected.connect(self.reject)

		layout = QVBoxLayout(self)
		layout.addWidget(QLabel("フィルター文字列:"))
		layout.addWidget(self.query_edit)
		layout.addWidget(buttons)

	def filter_text(self) -> str:
		return self.query_edit.text()

	def move_near(self, widget: QWidget) -> None:
		self.adjustSize()
		anchor = widget.mapToGlobal(QPoint(8, 8))
		screen = QGuiApplication.screenAt(anchor)
		if screen is None:
			screen = QGuiApplication.primaryScreen()
		if screen is None:
			self.move(anchor)
			return

		available = screen.availableGeometry()
		max_x = available.right() - self.width() + 1
		max_y = available.bottom() - self.height() + 1
		self.move(
			max(available.left(), min(anchor.x(), max_x)),
			max(available.top(), min(anchor.y(), max_y)),
		)


class ArchiveDialog(QDialog):
	def __init__(
		self,
		destination: str | Path,
		default_name: str,
		parent=None,
	):
		super().__init__(parent)
		self.setWindowTitle("アーカイブ作成")

		self.destination_label = QLabel(str(destination), self)
		self.name_edit = QLineEdit(default_name, self)
		self.password_edit = QLineEdit(self)
		self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
		self.password_edit.setPlaceholderText("空欄でパスワードなし")

		form = QFormLayout()
		form.addRow("保存先:", self.destination_label)
		form.addRow("ファイル名:", self.name_edit)
		form.addRow("パスワード:", self.password_edit)

		buttons = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Ok
			| QDialogButtonBox.StandardButton.Cancel,
			parent=self,
		)
		buttons.accepted.connect(self.accept)
		buttons.rejected.connect(self.reject)

		layout = QVBoxLayout(self)
		layout.addLayout(form)
		layout.addWidget(
			QLabel("パスワードはzip/jar/apkまたは7zで使用できます。", self)
		)
		layout.addWidget(buttons)

		self.name_edit.selectAll()
		self.name_edit.setFocus()

	def accept(self) -> None:
		if not self.name_edit.text().strip():
			self.name_edit.setFocus()
			return
		super().accept()

	def values(self) -> tuple[str, str]:
		return self.name_edit.text().strip(), self.password_edit.text()


class BookmarkDialog(QDialog):
	def __init__(self, bookmarks: list[str], parent=None):
		super().__init__(parent)
		self.setWindowTitle("ブックマーク")
		self.resize(760, 520)
		self._bookmarks = [Path(b) for b in bookmarks]

		self.filter_edit = QLineEdit(self)
		self.filter_edit.setPlaceholderText("ブックマークを絞り込む（F）")
		self.filter_edit.textChanged.connect(self._apply_filter)

		self.list_widget = QListWidget(self)
		self.list_widget.setAlternatingRowColors(True)
		self.list_widget.setTextElideMode(Qt.TextElideMode.ElideNone)
		self.list_widget.setHorizontalScrollBarPolicy(
			Qt.ScrollBarPolicy.ScrollBarAsNeeded
		)
		self.list_widget.installEventFilter(self)
		self.list_widget.itemDoubleClicked.connect(lambda _: self._accept_selected())

		buttons = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Ok
			| QDialogButtonBox.StandardButton.Cancel,
			parent=self,
		)
		buttons.accepted.connect(self._accept_selected)
		buttons.rejected.connect(self.reject)

		layout = QVBoxLayout(self)
		layout.addWidget(self.filter_edit)
		layout.addWidget(self.list_widget)
		layout.addWidget(buttons)
		self._apply_filter("")
		self.list_widget.setFocus()

	def _apply_filter(self, query: str) -> None:
		query = query.strip().casefold()
		self.list_widget.clear()
		for path in self._bookmarks:
			if query and query not in str(path).casefold():
				continue
			item = QListWidgetItem(str(path))
			item.setData(Qt.ItemDataRole.UserRole, str(path))
			self.list_widget.addItem(item)
		if self.list_widget.count() > 0:
			self.list_widget.setCurrentRow(0)

	def _accept_selected(self) -> None:
		if self.list_widget.currentItem() is None:
			return
		self.accept()

	def selected_path(self) -> Path | None:
		item = self.list_widget.currentItem()
		if item is None:
			return None
		value = item.data(Qt.ItemDataRole.UserRole)
		return Path(value) if value else None

	def eventFilter(self, watched, event) -> bool:
		if watched is self.list_widget and event.type() == QEvent.Type.KeyPress:
			if (
				event.key() == Qt.Key.Key_F
				and event.modifiers() == Qt.KeyboardModifier.NoModifier
			):
				self.filter_edit.setFocus()
				self.filter_edit.selectAll()
				return True
		return super().eventFilter(watched, event)


def _fmt_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB ({size:,} バイト)"
    if size < 1024 ** 3:
        return f"{size / (1024 ** 2):.1f} MB ({size:,} バイト)"
    return f"{size / (1024 ** 3):.1f} GB ({size:,} バイト)"


class FileInfoDialog(QDialog):
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ファイル情報")
        self.setMinimumWidth(480)

        from datetime import datetime

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("名前:", QLabel(path.name))
        form.addRow("場所:", QLabel(str(path.parent)))

        try:
            st = path.stat()
        except OSError:
            st = None

        if st is not None:
            if path.is_file():
                form.addRow("サイズ:", QLabel(_fmt_file_size(st.st_size)))
            form.addRow("更新日時:", QLabel(
                datetime.fromtimestamp(st.st_mtime).strftime("%Y/%m/%d %H:%M:%S")
            ))
            form.addRow("作成日時:", QLabel(
                datetime.fromtimestamp(st.st_ctime).strftime("%Y/%m/%d %H:%M:%S")
            ))
            if hasattr(st, "st_file_attributes"):
                attrs = []
                fa = st.st_file_attributes
                if fa & 0x01:
                    attrs.append("読み取り専用")
                if fa & 0x02:
                    attrs.append("隠しファイル")
                if fa & 0x04:
                    attrs.append("システム")
                if attrs:
                    form.addRow("属性:", QLabel(", ".join(attrs)))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, parent=self)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)


IMAGE_EXTENSIONS = {
    ".bmp", ".gif", ".jpg", ".jpeg", ".png",
    ".tga", ".tif", ".tiff", ".webp",
}


class ImageViewerDialog(QDialog):
    def __init__(self, paths: list[Path], index: int, parent=None):
        super().__init__(parent)
        self._paths = paths
        self._index = index
        self._pixmap: QPixmap | None = None

        self.setWindowTitle("画像ビューア")
        self.resize(900, 700)

        self._image_label = QLabel(self)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(1, 1)
        self._image_label.setStyleSheet("background: black;")

        self._info_label = QLabel(self)
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        layout.addWidget(self._image_label, 1)
        layout.addWidget(self._info_label)

        self._load_image()

    def _load_image(self) -> None:
        path = self._paths[self._index]
        self._pixmap = QPixmap(str(path))
        if self._pixmap.isNull():
            self._pixmap = None
            self._image_label.setText("読み込めませんでした")
        else:
            self._update_display()
        self._info_label.setText(
            f"{path.name}  ({self._index + 1} / {len(self._paths)})"
        )
        self.setWindowTitle(f"画像ビューア - {path.name}")

    def _update_display(self) -> None:
        if self._pixmap is None:
            return
        scaled = self._pixmap.scaled(
            self._image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_display()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Space):
            self._navigate(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._navigate(-1)
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Q, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
        else:
            super().keyPressEvent(event)

    def _navigate(self, delta: int) -> None:
        new_index = (self._index + delta) % len(self._paths)
        if new_index != self._index:
            self._index = new_index
            self._load_image()
