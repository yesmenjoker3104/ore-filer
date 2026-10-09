import difflib
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QThread, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
	QAbstractItemView,
	QButtonGroup,
	QCheckBox,
	QDialog,
	QDialogButtonBox,
	QFormLayout,
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QListWidget,
	QListWidgetItem,
	QPlainTextEdit,
	QPushButton,
	QRadioButton,
	QSpinBox,
	QSplitter,
	QTableWidget,
	QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
	QVBoxLayout,
	QWidget,
)

from ore_filer.services.search_service import find_paths, grep_files, GrepMatch


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
MARKDOWN_EXTENSIONS = {
    ".md", ".markdown", ".mdown", ".mkd",
}
HTML_EXTENSIONS = {
    ".html", ".htm",
}
PDF_EXTENSIONS = {
    ".pdf",
}
CSV_EXTENSIONS = {
    ".csv", ".tsv",
}
JSON_EXTENSIONS = {
    ".json", ".jsonl",
}
XML_EXTENSIONS = {
    ".xml", ".xhtml", ".xsd", ".xsl", ".xslt", ".wsdl",
}
SVG_EXTENSIONS = {
    ".svg",
}
LOG_EXTENSIONS = {
    ".log",
}
PLAINTEXT_EXTENSIONS = {
    ".yaml", ".yml", ".toml", ".env", ".properties", ".ini", ".cfg", ".conf",
}
HEX_EXTENSIONS = {
    ".bin", ".dat", ".exe", ".dll", ".so", ".dylib", ".class", ".pyc",
    ".obj", ".o", ".a", ".lib", ".pdb", ".iso", ".img", ".rom",
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



def confirm_list(
    parent,
    title: str,
    message: str,
    items: list[str],
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

    layout = QVBoxLayout(dialog)
    layout.addWidget(label)
    layout.addWidget(list_widget, 1)
    layout.addWidget(QLabel(f"{len(items)}件"))
    layout.addWidget(buttons)

    dialog.resize(520, 420)
    return dialog.exec() == QDialog.DialogCode.Accepted

# ── マークダウンビューア ──────────────────────────────────────
class MarkdownViewerDialog(QDialog):
    def __init__(
        self,
        path: Path,
        text: str,
        encoding: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"MarkdownViewer - {path.name}  [{encoding}]")
        self.resize(900, 650)

        viewer = QTextBrowser(self)
        viewer.setOpenExternalLinks(True)
        viewer.setMarkdown(text)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        layout.addWidget(viewer)

    def keyPressEvent(self, event) -> None:
        if event.key() in (
             Qt.Key.Key_Escape,
             Qt.Key.Key_Q,
             ):
            self.accept()
            return
        super().keyPressEvent(event)


# ── HTML ビューア ────────────────────────────────────────


class HtmlViewerDialog(QDialog):
    """HTML ファイルの簡易ビューア（QTextBrowser ベース）。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"HTML Viewer - {path.name}")
        self.resize(960, 700)

        viewer = QTextBrowser(self)
        viewer.setOpenExternalLinks(True)
        viewer.setSearchPaths([str(path.parent)])
        try:
            html = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            html = f"<pre>読み込みに失敗しました: {e}</pre>"
        viewer.setHtml(html)

        hint = QLabel("Esc / Q: 閉じる", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(viewer, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── PDF ビューア ─────────────────────────────────────────


class PdfViewerDialog(QDialog):
    """PDF ファイルのビューア（Qt PDF ベース）。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"PDF Viewer - {path.name}")
        self.resize(960, 800)

        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPdfWidgets import QPdfView

        self._doc = QPdfDocument(self)
        self._doc.load(str(path))

        view = QPdfView(self)
        view.setDocument(self._doc)
        view.setPageMode(QPdfView.PageMode.MultiPage)
        view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        hint = QLabel("Esc / Q: 閉じる", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(view, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── CSV ビューア ─────────────────────────────────────────


class CsvViewerDialog(QDialog):
    """CSV / TSV ファイルのテーブルビューア。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"CSV Viewer - {path.name}")
        self.resize(1000, 650)

        import csv as _csv

        delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            QMessageBox.critical(parent, "CSV Viewer", f"読み込みに失敗しました：\n{e}")
            return

        rows = list(_csv.reader(text.splitlines(), delimiter=delimiter))
        if not rows:
            rows = [[]]

        table = QTableWidget(len(rows) - 1, len(rows[0]), self)
        table.setHorizontalHeaderLabels(rows[0])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setDefaultSectionSize(22)

        for r, row in enumerate(rows[1:]):
            for c, cell in enumerate(row):
                if c < table.columnCount():
                    table.setItem(r, c, QTableWidgetItem(cell))

        row_label = QLabel(f"{len(rows) - 1} 行  |  {len(rows[0])} 列", self)
        hint = QLabel("Esc / Q: 閉じる", self)
        bottom = QHBoxLayout()
        bottom.addWidget(row_label)
        bottom.addStretch()
        bottom.addWidget(hint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(table, 1)
        layout.addLayout(bottom)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── JSON ビューア ────────────────────────────────────────


class JsonViewerDialog(QDialog):
    """JSON / JSONL ファイルのフォーマット済みビューア。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"JSON Viewer - {path.name}")
        self.resize(960, 700)

        import json as _json

        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            text = f"読み込みに失敗しました: {e}"

        if path.suffix.casefold() == ".jsonl":
            lines = [l for l in text.splitlines() if l.strip()]
            parts = []
            for i, line in enumerate(lines):
                try:
                    parts.append(_json.dumps(_json.loads(line), ensure_ascii=False, indent=2))
                except _json.JSONDecodeError:
                    parts.append(line)
            formatted = "\n".join(parts)
        else:
            try:
                formatted = _json.dumps(_json.loads(text), ensure_ascii=False, indent=2)
            except _json.JSONDecodeError as e:
                formatted = f"// JSON パースエラー: {e}\n\n{text}"

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        editor = QPlainTextEdit(self)
        editor.setReadOnly(True)
        editor.setFont(font)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText(formatted)

        hint = QLabel("Esc / Q: 閉じる", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(editor, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── XML ビューア ─────────────────────────────────────────


class XmlViewerDialog(QDialog):
    """XML ファイルのフォーマット済みビューア。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"XML Viewer - {path.name}")
        self.resize(960, 700)

        import xml.dom.minidom as _minidom

        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
            formatted = _minidom.parseString(raw.encode("utf-8")).toprettyxml(indent="  ")
            # toprettyxml が先頭に余分な宣言行を追加する場合があるのでそのまま利用
        except Exception as e:
            formatted = f"<!-- XML パースエラー: {e} -->\n\n{raw if 'raw' in dir() else ''}"

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        editor = QPlainTextEdit(self)
        editor.setReadOnly(True)
        editor.setFont(font)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText(formatted)

        hint = QLabel("Esc / Q: 閉じる", self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(editor, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── SVG ビューア ─────────────────────────────────────────


class SvgViewerDialog(QDialog):
    """SVG ファイルのベクタービューア。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"SVG Viewer - {path.name}")
        self.resize(800, 700)

        from PySide6.QtSvgWidgets import QSvgWidget

        svg_widget = QSvgWidget(str(path), self)
        svg_widget.setStyleSheet("background: white;")

        hint = QLabel("Esc / Q: 閉じる", self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(svg_widget, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── Log ビューア ─────────────────────────────────────────


class LogViewerDialog(QDialog):
    """ログファイルのビューア（重大度で行を色分け）。"""

    _LEVELS = [
        (re.compile(r"\b(FATAL|CRITICAL|SEVERE)\b", re.IGNORECASE), "#5a1a1a", "#ff9090"),
        (re.compile(r"\bERROR\b",                   re.IGNORECASE), "#3a1e1e", "#e8b5b5"),
        (re.compile(r"\bWARN(ING)?\b",              re.IGNORECASE), "#3a2e00", "#f0d040"),
        (re.compile(r"\bINFO\b",                    re.IGNORECASE), "#1e2a3a", "#9ac0e8"),
        (re.compile(r"\bDEBUG\b",                   re.IGNORECASE), "#2a2a2a", "#888888"),
    ]

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Log Viewer - {path.name}")
        self.resize(1100, 700)

        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as e:
            text = f"読み込みに失敗しました: {e}"

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor = QPlainTextEdit(self)
        self._editor.setReadOnly(True)
        self._editor.setFont(font)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.setPlainText(text)
        self._apply_log_colors()

        hint = QLabel("Esc / Q: 閉じる", self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(self._editor, 1)
        layout.addWidget(hint)

    def _apply_log_colors(self) -> None:
        from PySide6.QtGui import QTextCharFormat, QTextCursor

        fmts = []
        for _, bg, fg in self._LEVELS:
            f = QTextCharFormat()
            f.setBackground(QColor(bg))
            f.setForeground(QColor(fg))
            fmts.append(f)

        selections = []
        block = self._editor.document().begin()
        while block.isValid():
            text = block.text()
            for i, (pat, _, _) in enumerate(self._LEVELS):
                if pat.search(text):
                    sel = QTextEdit.ExtraSelection()
                    cur = QTextCursor(block)
                    cur.select(QTextCursor.SelectionType.LineUnderCursor)
                    sel.cursor = cur
                    sel.format = fmts[i]
                    selections.append(sel)
                    break
            block = block.next()
        self._editor.setExtraSelections(selections)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── Hex ビューア ─────────────────────────────────────────


class HexViewerDialog(QDialog):
    """バイナリファイルの16進数ダンプビューア。"""

    _CHUNK = 65536  # 最大表示バイト数

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Hex Viewer - {path.name}")
        self.resize(900, 700)

        try:
            data = path.read_bytes()
            truncated = len(data) > self._CHUNK
            data = data[:self._CHUNK]
        except OSError as e:
            data = b""
            truncated = False
            notice = f"読み込みに失敗しました: {e}"
        else:
            notice = f"先頭 {self._CHUNK:,} バイトを表示  （合計: {path.stat().st_size:,} バイト）" if truncated else f"{len(data):,} バイト"

        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            hex_part = f"{hex_part:<47}"
            ascii_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in chunk)
            lines.append(f"{offset:08X}  {hex_part}  {ascii_part}")

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        editor = QPlainTextEdit(self)
        editor.setReadOnly(True)
        editor.setFont(font)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setPlainText("\n".join(lines))

        status = QLabel(notice, self)
        hint = QLabel("Esc / Q: 閉じる", self)
        bottom = QHBoxLayout()
        bottom.addWidget(status)
        bottom.addStretch()
        bottom.addWidget(hint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(editor, 1)
        layout.addLayout(bottom)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── テキストビューア ──────────────────────────────────────


class TextViewerDialog(QDialog):
    def __init__(
        self,
        path: Path,
        text: str,
        encoding: str,
        highlight_lines: list[int] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._hits = sorted(set(highlight_lines)) if highlight_lines else []
        self._hit_index = 0
        self._path = path
        self._encoding = encoding
        self._grep_selections: list = []   # GREPハイライト
        self._search_selections: list = [] # テキスト検索ハイライト
        self.resize(900, 650)

        self._editor = QPlainTextEdit(self)
        self._editor.setReadOnly(True)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(font)
        self._editor.setPlainText(text)

        # ── 検索バー（下部、非表示で開始）──
        self._search_bar = QWidget(self)
        bar_layout = QHBoxLayout(self._search_bar)
        bar_layout.setContentsMargins(4, 2, 4, 2)
        bar_layout.setSpacing(4)
        self._search_edit = QLineEdit(self._search_bar)
        self._search_edit.setPlaceholderText("検索 (Enter: 次へ  Shift+Enter: 前へ  Esc: 閉じる)")
        self._search_label = QLabel("", self._search_bar)
        self._search_label.setFixedWidth(80)
        bar_layout.addWidget(self._search_edit)
        bar_layout.addWidget(self._search_label)
        self._search_bar.setVisible(False)
        self._search_edit.textChanged.connect(self._on_search_text_changed)
        self._search_edit.installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        layout.addWidget(self._editor)
        layout.addWidget(self._search_bar)

        if self._hits:
            self._apply_grep_highlights()
            self._jump_to_hit(0)
        else:
            self._editor.moveCursor(self._editor.textCursor().MoveOperation.Start)
        self._update_title()

    def _update_title(self) -> None:
        base = f"{self._path.name}  [{self._encoding}]"
        if self._hits:
            self.setWindowTitle(f"{base}  [{self._hit_index + 1}/{len(self._hits)}]")
        else:
            self.setWindowTitle(base)

    def _apply_grep_highlights(self) -> None:
        from PySide6.QtGui import QTextCharFormat, QTextCursor
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#ffff99"))
        selections = []
        doc = self._editor.document()
        for lineno in self._hits:
            block = doc.findBlockByLineNumber(lineno - 1)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(block)
            cur.select(QTextCursor.SelectionType.LineUnderCursor)
            sel.cursor = cur
            sel.format = fmt
            selections.append(sel)
        self._grep_selections = selections
        self._editor.setExtraSelections(self._grep_selections + self._search_selections)

    def _jump_to_hit(self, index: int) -> None:
        from PySide6.QtGui import QTextCursor
        if not self._hits:
            return
        self._hit_index = max(0, min(index, len(self._hits) - 1))
        lineno = self._hits[self._hit_index]
        doc = self._editor.document()
        block = doc.findBlockByLineNumber(lineno - 1)
        if block.isValid():
            cur = self._editor.textCursor()
            cur.setPosition(block.position())
            self._editor.setTextCursor(cur)
            self._editor.centerCursor()
        self._update_title()

    # ── テキスト検索 ──────────────────────────────────────

    def _open_search_bar(self) -> None:
        self._search_bar.setVisible(True)
        self._search_edit.setFocus()
        self._search_edit.selectAll()

    def _close_search_bar(self) -> None:
        self._search_bar.setVisible(False)
        self._search_selections = []
        self._editor.setExtraSelections(self._grep_selections)
        self._search_label.setText("")
        self._editor.setFocus()

    def _on_search_text_changed(self, text: str) -> None:
        from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextDocument
        self._search_selections = []
        if not text:
            self._editor.setExtraSelections(self._grep_selections)
            self._search_label.setText("")
            return
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#b3d9ff"))
        doc = self._editor.document()
        cursor = QTextCursor(doc)
        count = 0
        while True:
            cursor = doc.find(text, cursor)
            if cursor.isNull():
                break
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.format = fmt
            self._search_selections.append(sel)
            count += 1
        self._editor.setExtraSelections(self._grep_selections + self._search_selections)
        self._search_label.setText(f"{count} 件" if count else "見つからない")
        # 最初のマッチへスクロール
        if self._search_selections:
            self._editor.setTextCursor(self._search_selections[0].cursor)
            self._editor.centerCursor()

    def _search_next(self, backward: bool = False) -> None:
        from PySide6.QtGui import QTextDocument
        text = self._search_edit.text()
        if not text:
            return
        flags = QTextDocument.FindFlag(0)
        if backward:
            flags |= QTextDocument.FindFlag.FindBackward
        if not self._editor.find(text, flags):
            # 折り返し
            cur = self._editor.textCursor()
            cur.movePosition(
                cur.MoveOperation.End if backward else cur.MoveOperation.Start
            )
            self._editor.setTextCursor(cur)
            self._editor.find(text, flags)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        mods = event.modifiers()
        no_mod = mods == Qt.KeyboardModifier.NoModifier
        if self._search_bar.isVisible():
            # 検索バーが開いているときは Esc だけ横取り
            if key == Qt.Key.Key_Escape:
                self._close_search_bar()
            else:
                super().keyPressEvent(event)
            return
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q, Qt.Key.Key_V,
                   Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
        elif (key == Qt.Key.Key_F and mods == Qt.KeyboardModifier.ControlModifier) or \
             (key == Qt.Key.Key_Slash and no_mod):
            self._open_search_bar()
        elif key == Qt.Key.Key_N and self._hits and no_mod:
            self._jump_to_hit(self._hit_index + 1)
        elif key == Qt.Key.Key_N and self._hits and mods == Qt.KeyboardModifier.ShiftModifier:
            self._jump_to_hit(self._hit_index - 1)
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:
        """検索バーの Enter / Shift+Enter を横取りして前後検索。"""
        from PySide6.QtCore import QEvent
        if watched is self._search_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._search_next(backward=mods == Qt.KeyboardModifier.ShiftModifier)
                return True
        return super().eventFilter(watched, event)


# ── 一括リネーム ──────────────────────────────────────────

_INVALID_NAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _validate_new_name(name: str) -> str | None:
    """問題があれば理由文字列を返し、OK なら None を返す。"""
    if not name or not name.strip():
        return "空白のみ"
    if _INVALID_NAME_CHARS.search(name):
        return "使えない文字を含む"
    return None


class BulkRenameDialog(QDialog):
    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self.setWindowTitle("一括リネーム")
        self.resize(760, 560)
        self._paths = list(paths)
        self._result_pairs: list[tuple[Path, str]] = []

        # ── モード選択 ──
        self._mode_replace = QRadioButton("置換", self)
        self._mode_replace.setChecked(True)
        self._mode_seq = QRadioButton("連番", self)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self._mode_replace)
        mode_group.addButton(self._mode_seq)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._mode_replace)
        mode_row.addWidget(self._mode_seq)
        mode_row.addStretch()

        # ── 置換パネル ──
        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("検索文字列")
        self._replace_edit = QLineEdit(self)
        self._replace_edit.setPlaceholderText("置換文字列")
        self._regex_check = QCheckBox("正規表現", self)
        replace_form = QFormLayout()
        replace_form.addRow("検索:", self._search_edit)
        replace_form.addRow("置換:", self._replace_edit)
        replace_form.addRow("", self._regex_check)

        self._replace_panel = QWidget(self)
        self._replace_panel.setLayout(replace_form)

        # ── 連番パネル ──
        self._template_edit = QLineEdit("{name}_{n:03}{ext}", self)
        self._template_edit.setPlaceholderText("{name}_{n:03}{ext}")
        self._start_spin = QSpinBox(self)
        self._start_spin.setRange(0, 99999)
        self._start_spin.setValue(1)
        seq_form = QFormLayout()
        seq_form.addRow("書式:", self._template_edit)
        seq_form.addRow("開始番号:", self._start_spin)
        hint = QLabel(
            "変数: {name}=元の名前(拡張子なし), {ext}=拡張子(.付き), {n}=連番, {N}=総数",
            self,
        )
        hint.setWordWrap(True)
        seq_layout = QVBoxLayout()
        seq_layout.addLayout(seq_form)
        seq_layout.addWidget(hint)

        self._seq_panel = QWidget(self)
        self._seq_panel.setLayout(seq_layout)
        self._seq_panel.setVisible(False)

        # ── プレビュー表 ──
        self._table = QTableWidget(len(self._paths), 2, self)
        self._table.setHorizontalHeaderLabels(["変更前", "変更後"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 300)
        for row, path in enumerate(self._paths):
            self._table.setItem(row, 0, QTableWidgetItem(path.name))
            self._table.setItem(row, 1, QTableWidgetItem(path.name))

        # ── ボタン ──
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self._ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(mode_row)
        layout.addWidget(self._replace_panel)
        layout.addWidget(self._seq_panel)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._buttons)

        # シグナル接続
        self._mode_replace.toggled.connect(self._on_mode_changed)
        self._search_edit.textChanged.connect(self._update_preview)
        self._replace_edit.textChanged.connect(self._update_preview)
        self._regex_check.toggled.connect(self._update_preview)
        self._template_edit.textChanged.connect(self._update_preview)
        self._start_spin.valueChanged.connect(self._update_preview)

        self._update_preview()

    def _on_mode_changed(self, is_replace: bool) -> None:
        self._replace_panel.setVisible(is_replace)
        self._seq_panel.setVisible(not is_replace)
        self._update_preview()

    def _compute_new_names(self) -> list[str]:
        new_names: list[str] = []
        if self._mode_replace.isChecked():
            search = self._search_edit.text()
            replace = self._replace_edit.text()
            use_regex = self._regex_check.isChecked()
            for path in self._paths:
                try:
                    if use_regex and search:
                        new_name = re.sub(search, replace, path.name)
                    elif search:
                        new_name = path.name.replace(search, replace)
                    else:
                        new_name = path.name
                except re.error:
                    new_name = path.name
                new_names.append(new_name)
        else:
            template = self._template_edit.text()
            start = self._start_spin.value()
            total = len(self._paths)
            for i, path in enumerate(self._paths):
                n = start + i
                try:
                    new_name = template.format(
                        name=path.stem,
                        ext=path.suffix,
                        n=n,
                        N=total,
                    )
                except (KeyError, ValueError):
                    new_name = path.name
                new_names.append(new_name)
        return new_names

    def _update_preview(self) -> None:
        new_names = self._compute_new_names()
        error_color = QBrush(QColor(255, 80, 80, 120))
        ok_color = QBrush(QColor(0, 0, 0, 0))

        # 重複チェック（変更後名同士）
        name_count: dict[str, int] = {}
        for name in new_names:
            name_count[name] = name_count.get(name, 0) + 1

        has_error = False
        existing_names = {p.name.casefold() for p in self._paths}

        for row, (path, new_name) in enumerate(zip(self._paths, new_names)):
            item = self._table.item(row, 1)
            if item is None:
                item = QTableWidgetItem()
                self._table.setItem(row, 1, item)
            item.setText(new_name)

            error = _validate_new_name(new_name)
            if error is None and name_count.get(new_name, 0) > 1:
                error = "重複"
            # 自分以外の既存ファイルと重複しているか（リネーム先が別ファイルと被る）
            if error is None and new_name.casefold() != path.name.casefold():
                parent = path.parent
                if (parent / new_name).exists() and new_name.casefold() not in existing_names:
                    error = "既に存在"

            color = error_color if error else ok_color
            for col in range(2):
                cell = self._table.item(row, col)
                if cell:
                    cell.setBackground(color)
                    if error and col == 1:
                        cell.setToolTip(error)
                    else:
                        cell.setToolTip("")
            if error:
                has_error = True

        self._ok_btn.setEnabled(not has_error)

    def _on_accept(self) -> None:
        new_names = self._compute_new_names()
        self._result_pairs = [
            (path, new_name)
            for path, new_name in zip(self._paths, new_names)
            if path.name != new_name
        ]
        self.accept()

    def result_pairs(self) -> list[tuple[Path, str]]:
        return self._result_pairs


# ── 左右ペイン比較選択 ────────────────────────────────────


class CompareSelectDialog(QDialog):
    ONLY_LEFT = "only_left"
    ONLY_RIGHT = "only_right"
    NEWER = "newer"
    DIFF_SIZE = "diff_size"
    SAME_NAME = "same_name"

    def __init__(self, left_label: str, right_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("比較選択")
        self._mode: str | None = None

        desc = QLabel(
            f"左: {left_label}\n右: {right_label}\n\n"
            "選択するファイルの条件を選んでください。",
            self,
        )
        desc.setWordWrap(True)

        self._rb_only_left = QRadioButton(f"左にしか無いファイル", self)
        self._rb_only_right = QRadioButton(f"右にしか無いファイル", self)
        self._rb_newer = QRadioButton("同名で更新日時が新しい方（両ペインで選択）", self)
        self._rb_diff_size = QRadioButton("同名でサイズが違うファイル（両ペインで選択）", self)
        self._rb_same_name = QRadioButton("同名ファイルをすべて選択（両ペインで選択）", self)
        self._rb_only_left.setChecked(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(desc)
        for rb in (
            self._rb_only_left,
            self._rb_only_right,
            self._rb_newer,
            self._rb_diff_size,
            self._rb_same_name,
        ):
            layout.addWidget(rb)
        layout.addStretch()
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        for rb, mode in (
            (self._rb_only_left, self.ONLY_LEFT),
            (self._rb_only_right, self.ONLY_RIGHT),
            (self._rb_newer, self.NEWER),
            (self._rb_diff_size, self.DIFF_SIZE),
            (self._rb_same_name, self.SAME_NAME),
        ):
            if rb.isChecked():
                self._mode = mode
                break
        self.accept()

    def mode(self) -> str | None:
        return self._mode


# ── フォルダサイズ計算スレッド ────────────────────────────


class DirSizeThread(QThread):
    finished_with = Signal(list)   # list of (path, bytes, count)
    progress = Signal(str)

    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self._paths = list(paths)

    def run(self) -> None:
        from ore_filer.services.file_operations import calc_dir_size
        results: list[tuple[Path, int, int]] = []
        for path in self._paths:
            self.progress.emit(f"計算中: {path.name}")
            total_bytes, total_files = calc_dir_size(path, self.isInterruptionRequested)
            results.append((path, total_bytes, total_files))
        self.finished_with.emit(results)


# ── GREP ──────────────────────────────────────────────────────


class GrepThread(QThread):
    match_found = Signal(object)   # GrepMatch
    finished_grep = Signal(list)   # list[GrepMatch]
    error = Signal(str)

    def __init__(
        self,
        root: Path,
        pattern: str,
        *,
        recursive: bool = True,
        use_regex: bool = False,
        ignore_case: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.root = root
        self.pattern = pattern
        self.recursive = recursive
        self.use_regex = use_regex
        self.ignore_case = ignore_case

    def run(self) -> None:
        try:
            results = grep_files(
                self.root,
                self.pattern,
                recursive=self.recursive,
                use_regex=self.use_regex,
                ignore_case=self.ignore_case,
                is_cancelled=self.isInterruptionRequested,
            )
        except Exception as e:
            self.error.emit(str(e))
        else:
            self.finished_grep.emit(results)


class GrepDialog(QDialog):
    def __init__(self, root: str | Path, parent=None):
        super().__init__(parent)
        self.root = Path(root).expanduser().resolve()
        self._thread: GrepThread | None = None
        self._results: list[GrepMatch] = []
        self._closing = False

        self.setWindowTitle("テキスト GREP")
        self.resize(800, 560)

        self.pattern_edit = QLineEdit(self)
        self.pattern_edit.setPlaceholderText("検索パターン（正規表現も使用可）")
        self.search_btn = QPushButton("検索", self)
        self.search_btn.clicked.connect(self._start_search)
        self.pattern_edit.returnPressed.connect(self._start_search)

        search_row = QHBoxLayout()
        search_row.addWidget(self.pattern_edit)
        search_row.addWidget(self.search_btn)

        self.recursive_cb = QCheckBox("サブディレクトリを含む", self)
        self.recursive_cb.setChecked(True)
        self.regex_cb = QCheckBox("正規表現", self)
        self.ignorecase_cb = QCheckBox("大文字小文字を区別しない", self)
        self.ignorecase_cb.setChecked(True)

        opt_row = QHBoxLayout()
        opt_row.addWidget(self.recursive_cb)
        opt_row.addWidget(self.regex_cb)
        opt_row.addWidget(self.ignorecase_cb)
        opt_row.addStretch()

        self.status_label = QLabel(f"検索場所: {self.root}", self)
        self.result_list = QListWidget(self)
        self.result_list.setAlternatingRowColors(True)
        self.result_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.result_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.result_list.itemDoubleClicked.connect(self._accept_selected)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.ok_btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_btn.setEnabled(False)
        self.buttons.accepted.connect(self._accept_selected)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addLayout(opt_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.result_list, 1)
        layout.addWidget(self.buttons)

        self.pattern_edit.setFocus()

    def _start_search(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        pattern = self.pattern_edit.text().strip()
        if not pattern:
            self.status_label.setText("パターンを入力してください")
            return

        self._results = []
        self.result_list.clear()
        self.ok_btn.setEnabled(False)
        self.pattern_edit.setEnabled(False)
        self.search_btn.setEnabled(False)
        self.status_label.setText("検索中...")

        thread = GrepThread(
            self.root, pattern,
            recursive=self.recursive_cb.isChecked(),
            use_regex=self.regex_cb.isChecked(),
            ignore_case=self.ignorecase_cb.isChecked(),
            parent=self,
        )
        thread.finished_grep.connect(self._on_finished)
        thread.error.connect(self._on_error)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        thread.start()

    def _on_finished(self, results: list) -> None:
        if self._closing:
            return
        self._results = list(results)
        # ファイル単位でまとめて表示（cfiler 準拠）
        seen: dict = {}
        for m in results:
            if m.path not in seen:
                seen[m.path] = 0
            seen[m.path] += 1

        for path, count in seen.items():
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                rel = path
            item = QListWidgetItem(f"{rel}  ({count} 件)")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.result_list.addItem(item)

        if seen:
            self.result_list.setCurrentRow(0)
            self.ok_btn.setEnabled(True)
            self.status_label.setText(
                f"{len(seen)} ファイル / {len(results)} マッチ"
            )
        else:
            self.status_label.setText("見つかりませんでした")

    def _on_error(self, msg: str) -> None:
        if not self._closing:
            self.status_label.setText(f"エラー: {msg}")

    def _on_thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        if thread:
            thread.deleteLater()
        if self._closing:
            self.done(QDialog.DialogCode.Rejected)
            return
        self.pattern_edit.setEnabled(True)
        self.search_btn.setEnabled(True)

    def _accept_selected(self, _item=None) -> None:
        item = self.result_list.currentItem()
        if item is None:
            return
        self.accept()

    def matched_paths(self) -> list[Path]:
        """マッチしたファイルパス一覧（重複なし）を返す。"""
        seen: list[Path] = []
        seen_set: set[Path] = set()
        for m in self._results:
            if m.path not in seen_set:
                seen_set.add(m.path)
                seen.append(m.path)
        return seen

    def line_hits(self) -> dict[Path, list[int]]:
        """ファイルパス → 該当行番号リスト の辞書を返す。"""
        result: dict[Path, list[int]] = {}
        for m in self._results:
            result.setdefault(m.path, []).append(m.lineno)
        return result

    def reject(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._closing = True
            self.status_label.setText("検索を中止しています...")
            self.pattern_edit.setEnabled(False)
            self.search_btn.setEnabled(False)
            self.ok_btn.setEnabled(False)
            self._thread.requestInterruption()
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._thread is not None and self._thread.isRunning():
            self.reject()
            event.ignore()
            return
        super().closeEvent(event)


# ── 差分ダイアログ ───────────────────────────────────────────


class DiffDialog(QDialog):
    def __init__(self, left_name: str, right_name: str, diff_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"差分: {left_name}  ↔  {right_name}")
        self.resize(960, 700)

        self._editor = QPlainTextEdit(self)
        self._editor.setReadOnly(True)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(font)

        if diff_text:
            self._editor.setPlainText(diff_text)
            self._apply_diff_colors()
        else:
            self._editor.setPlainText("（差分なし: 同一内容です）")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._editor)

    def _apply_diff_colors(self) -> None:
        from PySide6.QtGui import QTextCharFormat, QTextCursor
        doc = self._editor.document()
        add_fmt = QTextCharFormat()
        add_fmt.setBackground(QColor("#1e3a1e"))
        add_fmt.setForeground(QColor("#b5e8b5"))
        del_fmt = QTextCharFormat()
        del_fmt.setBackground(QColor("#3a1e1e"))
        del_fmt.setForeground(QColor("#e8b5b5"))
        hunk_fmt = QTextCharFormat()
        hunk_fmt.setBackground(QColor("#1e2a3a"))
        hunk_fmt.setForeground(QColor("#9ac0e8"))
        selections = []
        block = doc.begin()
        while block.isValid():
            text = block.text()
            if text.startswith("@@"):
                fmt = hunk_fmt
            elif text.startswith("+") and not text.startswith("+++"):
                fmt = add_fmt
            elif text.startswith("-") and not text.startswith("---"):
                fmt = del_fmt
            else:
                block = block.next()
                continue
            sel = QTextEdit.ExtraSelection()
            cur = QTextCursor(block)
            cur.select(QTextCursor.SelectionType.LineUnderCursor)
            sel.cursor = cur
            sel.format = fmt
            selections.append(sel)
            block = block.next()
        self._editor.setExtraSelections(selections)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


class SideBySideDiffDialog(QDialog):
    """左右並べの差分ダイアログ。"""

    def __init__(
        self,
        left_name: str,
        right_name: str,
        left_text: str,
        right_text: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        has_diff = left_text != right_text
        title = f"差分: {left_name}  ↔  {right_name}"
        if not has_diff:
            title += "  （同一内容）"
        self.setWindowTitle(title)
        self.resize(1200, 700)

        left_lines = left_text.splitlines()
        right_lines = right_text.splitlines()

        # SequenceMatcher で行を整列
        aligned_left: list[tuple[int | None, str]] = []   # (lineno | None, text)
        aligned_right: list[tuple[int | None, str]] = []
        left_changed: list[bool] = []
        right_changed: list[bool] = []
        self._diff_blocks: list[int] = []  # 差分ブロック先頭の行インデックス

        matcher = difflib.SequenceMatcher(None, left_lines, right_lines, autojunk=False)
        l_no = r_no = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for k in range(i2 - i1):
                    aligned_left.append((l_no + k + 1, left_lines[i1 + k]))
                    aligned_right.append((r_no + k + 1, right_lines[j1 + k]))
                    left_changed.append(False)
                    right_changed.append(False)
                l_no += i2 - i1
                r_no += j2 - j1
            else:
                self._diff_blocks.append(len(aligned_left))
                lc, rc = i2 - i1, j2 - j1
                for k in range(max(lc, rc)):
                    aligned_left.append(
                        (l_no + k + 1, left_lines[i1 + k]) if k < lc else (None, "")
                    )
                    aligned_right.append(
                        (r_no + k + 1, right_lines[j1 + k]) if k < rc else (None, "")
                    )
                    left_changed.append(tag in ("replace", "delete") or (tag == "insert" and k >= lc))
                    right_changed.append(tag in ("replace", "insert") or (tag == "delete" and k >= rc))
                l_no += lc
                r_no += rc

        max_no = max(
            max((ln for ln, _ in aligned_left if ln is not None), default=0),
            max((ln for ln, _ in aligned_right if ln is not None), default=0),
        )
        w = len(str(max_no)) if max_no > 0 else 1

        def fmt(lineno: int | None, text: str) -> str:
            if lineno is None:
                return " " * (w + 1) + " "
            return f"{lineno:{w}d} | {text}"

        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)

        self._left_editor = QPlainTextEdit(self)
        self._left_editor.setReadOnly(True)
        self._left_editor.setFont(font)
        self._left_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._left_editor.setPlainText("\n".join(fmt(ln, t) for ln, t in aligned_left))

        self._right_editor = QPlainTextEdit(self)
        self._right_editor.setReadOnly(True)
        self._right_editor.setFont(font)
        self._right_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._right_editor.setPlainText("\n".join(fmt(ln, t) for ln, t in aligned_right))

        self._left_sels: list = self._apply_colors(self._left_editor, left_changed, aligned_left, is_right=False)
        self._right_sels: list = self._apply_colors(self._right_editor, right_changed, aligned_right, is_right=True)

        self._syncing = False
        for src, dst in (
            (self._left_editor, self._right_editor),
            (self._right_editor, self._left_editor),
        ):
            src.verticalScrollBar().valueChanged.connect(
                lambda v, d=dst: self._vsync(d, v)
            )
            src.horizontalScrollBar().valueChanged.connect(
                lambda v, d=dst: self._hsync(d, v)
            )

        self._cur_diff = -1

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        for editor, name in ((self._left_editor, left_name), (self._right_editor, right_name)):
            w_wrap = QWidget()
            vl = QVBoxLayout(w_wrap)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(0)
            vl.addWidget(QLabel(f"  {name}"))
            vl.addWidget(editor)
            splitter.addWidget(w_wrap)
        splitter.setSizes([600, 600])

        n = len(self._diff_blocks)
        base_hint = "N: 次の差分  P: 前の差分  Esc / Q: 閉じる"
        self._hint_label = QLabel(f"{base_hint}  ｜  差分 {n} 箇所", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._hint_label)

    def _apply_colors(
        self,
        editor: QPlainTextEdit,
        changed: list[bool],
        aligned: list[tuple[int | None, str]],
        *,
        is_right: bool,
    ) -> list:
        from PySide6.QtGui import QTextCharFormat, QTextCursor

        del_fmt = QTextCharFormat()
        del_fmt.setBackground(QColor("#3a1e1e"))
        del_fmt.setForeground(QColor("#e8b5b5"))

        add_fmt = QTextCharFormat()
        add_fmt.setBackground(QColor("#1e3a1e"))
        add_fmt.setForeground(QColor("#b5e8b5"))

        pad_fmt = QTextCharFormat()
        pad_fmt.setBackground(QColor("#2a2a2a"))
        pad_fmt.setForeground(QColor("#555555"))

        selections = []
        block = editor.document().begin()
        for row, is_changed in enumerate(changed):
            if not block.isValid():
                break
            if is_changed:
                lineno = aligned[row][0]
                fmt = pad_fmt if lineno is None else (add_fmt if is_right else del_fmt)
                sel = QTextEdit.ExtraSelection()
                cur = QTextCursor(block)
                cur.select(QTextCursor.SelectionType.LineUnderCursor)
                sel.cursor = cur
                sel.format = fmt
                selections.append(sel)
            block = block.next()
        editor.setExtraSelections(selections)
        return selections

    def _vsync(self, target: QPlainTextEdit, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        target.verticalScrollBar().setValue(value)
        self._syncing = False

    def _hsync(self, target: QPlainTextEdit, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        target.horizontalScrollBar().setValue(value)
        self._syncing = False

    def _jump_to(self, idx: int) -> None:
        if not self._diff_blocks:
            return
        from PySide6.QtGui import QTextCharFormat, QTextCursor, QFont as _QFont

        # カウンター更新
        n = len(self._diff_blocks)
        self._hint_label.setText(
            f"N: 次の差分  P: 前の差分  Esc / Q: 閉じる  ｜  差分 {idx + 1} / {n}"
        )

        # 現在ブロックの行範囲を特定（次の差分ブロック開始まで、または末尾まで）
        start_row = self._diff_blocks[idx]
        end_row = self._diff_blocks[idx + 1] if idx + 1 < n else 10**9

        cur_fmt = QTextCharFormat()
        cur_fmt.setBackground(QColor("#5a4a00"))
        cur_fmt.setForeground(QColor("#f0d040"))
        cur_fmt.setFontWeight(_QFont.Weight.Bold)

        def _current_sels(editor: QPlainTextEdit) -> list:
            sels = []
            doc = editor.document()
            for row in range(start_row, min(end_row, doc.blockCount())):
                blk = doc.findBlockByLineNumber(row)
                if not blk.isValid():
                    break
                sel = QTextEdit.ExtraSelection()
                c = QTextCursor(blk)
                c.select(QTextCursor.SelectionType.LineUnderCursor)
                sel.cursor = c
                sel.format = cur_fmt
                sels.append(sel)
            return sels

        self._left_editor.setExtraSelections(self._left_sels + _current_sels(self._left_editor))
        self._right_editor.setExtraSelections(self._right_sels + _current_sels(self._right_editor))

        block = self._left_editor.document().findBlockByLineNumber(start_row)
        if block.isValid():
            cur = QTextCursor(block)
            self._left_editor.setTextCursor(cur)
            self._left_editor.ensureCursorVisible()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        elif key == Qt.Key.Key_N and self._diff_blocks:
            self._cur_diff = (self._cur_diff + 1) % len(self._diff_blocks)
            self._jump_to(self._cur_diff)
        elif key == Qt.Key.Key_P and self._diff_blocks:
            self._cur_diff = (self._cur_diff - 1) % len(self._diff_blocks)
            self._jump_to(self._cur_diff)
        else:
            super().keyPressEvent(event)


# ── キー一覧ダイアログ ────────────────────────────────────────


class KeymapHelpDialog(QDialog):
    def __init__(self, bindings: list[tuple[str, str, str]], parent=None):
        """bindings: [(action, key_spec, description), ...]"""
        super().__init__(parent)
        self.setWindowTitle("キー一覧")
        self.resize(640, 600)

        table = QTableWidget(len(bindings), 3, self)
        table.setHorizontalHeaderLabels(["アクション", "キー", "説明"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 160)
        table.setColumnWidth(1, 120)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)

        for row, (action, key_spec, desc) in enumerate(bindings):
            table.setItem(row, 0, QTableWidgetItem(action))
            table.setItem(row, 1, QTableWidgetItem(key_spec))
            table.setItem(row, 2, QTableWidgetItem(desc))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, parent=self)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(table, 1)
        layout.addWidget(buttons)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q, Qt.Key.Key_Question):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── Git ───────────────────────────────────────────────────────


class GitStatusThread(QThread):
    """フォルダの Git 状態をバックグラウンドで取得するスレッド。

    finished_with を emit するとき:
      - pane_path: スレッド起動時のペインパス（文字列）
      - branch: ブランチ名（リポジトリ外は None）
      - status_map: {casefolded パス文字列: XY コード}
    """
    finished_with = Signal(str, object, dict)  # (pane_path, branch|None, status_map)

    def __init__(self, pane_path: Path, parent=None):
        super().__init__(parent)
        self._pane_path = pane_path

    def run(self) -> None:
        from ore_filer.services import git_service
        pane_str = str(self._pane_path)
        try:
            repo = git_service.find_repo_root(self._pane_path)
            if repo is None:
                self.finished_with.emit(pane_str, None, {})
                return
            branch = git_service.current_branch(repo)
            raw_map = git_service.status(repo)
            str_map = {str(k).casefold(): v for k, v in raw_map.items()}
            self.finished_with.emit(pane_str, branch, str_map)
        except Exception:
            self.finished_with.emit(pane_str, None, {})


class GitCommandThread(QThread):
    """任意の Git コマンドをバックグラウンドで実行するスレッド。"""
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self._func = func

    def run(self) -> None:
        try:
            result = self._func()
            self.succeeded.emit(result or "")
        except Exception as e:
            self.failed.emit(str(e))


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
        from PySide6.QtCore import QEvent
        if watched is self._msg_edit and event.type() == QEvent.Type.KeyPress:
            if (
                event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
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
