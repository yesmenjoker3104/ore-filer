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
	QTableWidget,
	QTableWidgetItem,
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


# ── テキストビューア ──────────────────────────────────────


class TextViewerDialog(QDialog):
    def __init__(self, path: Path, text: str, encoding: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{path.name}  [{encoding}]")
        self.resize(900, 650)

        self._editor = QPlainTextEdit(self)
        self._editor.setReadOnly(True)
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._editor.setFont(font)
        self._editor.setPlainText(text)
        self._editor.moveCursor(self._editor.textCursor().MoveOperation.Start)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._editor)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (
            Qt.Key.Key_Escape,
            Qt.Key.Key_Q,
            Qt.Key.Key_V,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ):
            self.accept()
        else:
            super().keyPressEvent(event)


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
