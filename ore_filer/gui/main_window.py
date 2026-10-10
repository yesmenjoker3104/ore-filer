import base64
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
	QDialog,
	QMainWindow,
	QSplitter,
	QMessageBox,
	QInputDialog,
	QLineEdit,
)

from ore_filer.gui.dialogs import (
	CSV_EXTENSIONS,
	CsvViewerDialog,
	HTML_EXTENSIONS,
	HtmlViewerDialog,
	JSON_EXTENSIONS,
	JsonViewerDialog,
	LOG_EXTENSIONS,
	LogViewerDialog,
	MARKDOWN_EXTENSIONS,
	PDF_EXTENSIONS,
	PdfViewerDialog,
	ArchiveDialog,
	BookmarkDialog,
	BulkRenameDialog,
	CompareSelectDialog,
	confirm_list,
	DiffDialog,
	DirSizeThread,
	FileInfoDialog,
	FilterDialog,
	GitCommandThread,
	GitCommitDialog,
	GitHttpCredentialsDialog,
	GitLogDialog,
	GitStatusThread,
	SideBySideDiffDialog,
	GrepDialog,
	HistoryDialog,
	IMAGE_EXTENSIONS,
	ImageViewerDialog,
	KeymapHelpDialog,
	HEX_EXTENSIONS,
	HexViewerDialog,
	PLAINTEXT_EXTENSIONS,
	SearchDialog,
	SortDialog,
	SVG_EXTENSIONS,
	SvgViewerDialog,
	TextViewerDialog,
	MarkdownViewerDialog,
	XML_EXTENSIONS,
	XmlViewerDialog,
)
from ore_filer.services import git_service
from ore_filer.gui.keymap import KeyMap, user_keymap_file
from ore_filer.gui.context_menu import show_shell_context_menu
from ore_filer.gui.pane import PaneWidget
from ore_filer.settings import load_bookmarks, load_config, load_session, save_bookmarks, save_config, save_session
from ore_filer.updater import UpdateCheckThread, UpdateDownloadThread, install_update
from ore_filer.services.file_operations import (
	ArchiveEntry,
	ArchivePasswordError,
	copy_paths,
	copy_paths_with_structure,
	copy_archive_entries,
	create_directory,
	create_file,
	delete_paths,
	disk_usage,
	reveal_in_explorer,
	read_text_pair,
	text_diff,
	trash_paths,
	is_archive_path,
	open_in_editor,
	open_terminal,
	read_text_preview,
	rename_path,
	rename_paths,
	move_paths,
	open_with_association,
	create_archive,
	extract_archive,
)


class FileOperationThread(QThread):
	completed = Signal()
	failed = Signal(str)
	progress = Signal(int, int)

	def __init__(self, func: Callable, parent=None):
		super().__init__(parent)
		self._func = func

	def run(self) -> None:
		try:
			self._func(self.progress.emit)
		except Exception as error:
			self.failed.emit(str(error))
		else:
			self.completed.emit()


class ArchiveThread(QThread):
	completed = Signal(str)
	failed = Signal(str)

	def __init__(
		self,
		archive_path: Path,
		base_path: Path,
		paths: list[Path],
		overwrite: bool,
		password: str,
		parent=None,
	):
		super().__init__(parent)
		self.archive_path = archive_path
		self.base_path = base_path
		self.paths = paths
		self.overwrite = overwrite
		self.password = password

	def run(self) -> None:
		try:
			archive_path = create_archive(
				self.archive_path,
				self.base_path,
				self.paths,
				overwrite=self.overwrite,
				password=self.password or None,
			)
		except Exception as error:
			self.failed.emit(str(error))
		else:
			self.completed.emit(str(archive_path))


class ArchiveCopyThread(QThread):
	completed = Signal(int)
	failed = Signal(str)

	def __init__(
		self,
		archive_path: Path,
		entries: list[ArchiveEntry],
		destination: Path,
		password: str | None,
		parent=None,
	):
		super().__init__(parent)
		self.archive_path = archive_path
		self.entries = entries
		self.destination = destination
		self.password = password

	def run(self) -> None:
		try:
			copied_count = copy_archive_entries(
				self.archive_path,
				self.entries,
				self.destination,
				password=self.password,
				overwrite=True,
			)
		except Exception as error:
			self.failed.emit(str(error))
		else:
			self.completed.emit(copied_count)


class ExtractArchiveThread(QThread):
	completed = Signal(int, str)
	failed = Signal(str)
	progress = Signal(int, int)

	def __init__(
		self,
		archive_paths: list[Path],
		destination: Path,
		parent=None,
	):
		super().__init__(parent)
		self.archive_paths = archive_paths
		self.destination = destination

	def run(self) -> None:
		extracted_count = 0
		errors: list[str] = []
		total = len(self.archive_paths)
		for i, archive_path in enumerate(self.archive_paths):
			try:
				extract_archive(archive_path, self.destination)
			except Exception as error:
				errors.append(f"{archive_path.name}: {error}")
			else:
				extracted_count += 1
			self.progress.emit(i + 1, total)

		if extracted_count == 0:
			self.failed.emit("\n".join(errors) or "展開できるアーカイブがありません。")
		else:
			self.completed.emit(extracted_count, "\n".join(errors))


def _fmt_size(size: int) -> str:
	if size < 1024:
		return f"{size} B"
	if size < 1024 * 1024:
		return f"{size / 1024:.1f} KB"
	if size < 1024 ** 3:
		return f"{size / (1024 ** 2):.1f} MB"
	return f"{size / (1024 ** 3):.1f} GB"


class MainWindow(QMainWindow):
	def __init__(
		self,
		left_path: str | Path,
		right_path: str | Path,
		history: list[str | Path] | None = None,
	):
		super().__init__()
		from ore_filer.version import __version__
		self.setWindowTitle(f"Ore Filer v{__version__}")
		self.resize(1200, 700)
		shared_history = history if history is not None else []
		self.left_pane = PaneWidget(left_path, history=shared_history)
		self.right_pane = PaneWidget(right_path, history=shared_history)

		self.panes = [self.left_pane, self.right_pane]
		self.active_pane_index = 0
		self._archive_thread: QThread | None = None
		self._file_op_thread: FileOperationThread | None = None
		self._dir_size_thread: DirSizeThread | None = None

		# キーマップ
		self._keymap = KeyMap()
		self._keymap_watcher = None
		self._setup_keymap_watcher()

		self.splitter = QSplitter(Qt.Orientation.Horizontal)
		self.splitter.addWidget(self.left_pane)
		self.splitter.addWidget(self.right_pane)
		self.splitter.setSizes([600, 600])

		self.setCentralWidget(self.splitter)

		for pane in self.panes:
			pane.file_view.installEventFilter(self)
			pane.file_view.viewport().installEventFilter(self)

		self._bookmarks: list[str] = load_bookmarks()
		self._bookmark_set: set[str] = {b.casefold() for b in self._bookmarks}
		for pane in self.panes:
			pane.bookmarks = self._bookmark_set
			pane.selection_changed.connect(self._update_status_bar)
			pane.path_changed.connect(self._update_status_bar)

		for pane in self.panes:
			pane.files_dropped.connect(self._on_files_dropped)

		self._update_active_pane()
		# 隠しファイル表示設定を復元（デフォルト: 表示）
		_cfg = load_config()
		_show_hidden = _cfg.get("show_hidden", True)
		for pane in self.panes:
			pane.set_show_hidden(_show_hidden)

		self._git_status_threads: dict[int, GitStatusThread] = {}
		self._git_windows: dict = {}
		for pane in self.panes:
			pane.path_changed.connect(
				lambda _path, p=pane: self._refresh_git_status(p)
			)

		self._last_update_check: str = ""
		self._update_check_thread: UpdateCheckThread | None = None
		self._update_download_thread: UpdateDownloadThread | None = None
		QTimer.singleShot(3000, self._check_for_updates)
		# 起動時に両ペインの Git 状態を取得
		for pane in self.panes:
			self._refresh_git_status(pane)

	@property
	def active_pane(self) -> PaneWidget:
		return self.panes[self.active_pane_index]

	@property
	def inactive_pane(self) -> PaneWidget:
		return self.panes[1 - self.active_pane_index]

	def focused_path(self) -> Path | None:
		index = self.active_pane.file_view.currentIndex()
		return self.active_pane.path_from_index(index)

	def move_to_inactive_path(self) -> None:
		self.active_pane.navigate_to(self.inactive_pane.current_path)

	def move_inactive_to_active_path(self) -> None:
		self.inactive_pane.navigate_to(self.active_pane.current_path)

	def closeEvent(self, event) -> None:
		geometry = base64.b64encode(bytes(self.saveGeometry())).decode()
		splitter = base64.b64encode(bytes(self.splitter.saveState())).decode()
		save_session(
			self.left_pane.history_paths(),
			left_path=str(self.left_pane.current_path),
			right_path=str(self.right_pane.current_path),
			geometry=geometry,
			splitter=splitter,
			last_update_check=self._last_update_check,
			font_size=self.left_pane._font_size,
		)
		super().closeEvent(event)

	def restore_font_size(self, size: int) -> None:
		if size > 0:
			for pane in self.panes:
				pane.adjust_font_size(size - pane._font_size)

	def restore_window_state(self, geometry: str, splitter: str) -> None:
		if geometry:
			try:
				self.restoreGeometry(base64.b64decode(geometry))
			except Exception:
				pass
		if splitter:
			try:
				self.splitter.restoreState(base64.b64decode(splitter))
			except Exception:
				pass

	def show_history(self) -> None:
		history = self.active_pane.history_paths()
		if not history:
			return

		dialog = HistoryDialog(history, self)
		if dialog.exec() == QDialog.DialogCode.Accepted:
			selected = dialog.selected_path()
			if selected is not None:
				self.active_pane.navigate_to(selected)

	def _toggle_bookmark(self) -> None:
		path = self.focused_path()
		if path is None:
			return
		key = str(path).casefold()
		if key in self._bookmark_set:
			self._bookmark_set.discard(key)
			self._bookmarks = [b for b in self._bookmarks if b.casefold() != key]
		else:
			self._bookmark_set.add(key)
			self._bookmarks.insert(0, str(path))
		save_bookmarks(self._bookmarks)
		for pane in self.panes:
			pane.file_view.viewport().update()

	def _show_bookmark_list(self, local: bool) -> None:
		if local:
			prefix = str(self.active_pane.current_path).casefold()
			items = [b for b in self._bookmarks if b.casefold().startswith(prefix)]
		else:
			items = list(self._bookmarks)
		if not items:
			return
		dialog = BookmarkDialog(items, self)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		selected = dialog.selected_path()
		if selected is None:
			return
		if selected.is_file():
			self.active_pane.navigate_to(selected.parent)
			self.active_pane.focus_name(selected.name)
		elif selected.is_dir():
			self.active_pane.navigate_to(selected)

	def search_files(self) -> None:
		if self.active_pane.is_archive_view():
			return
		dialog = SearchDialog(self.active_pane.current_path, self)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return

		results = dialog.result_paths()
		if results:
			self.active_pane.show_search_results(results)

	def sort_files(self) -> None:
		if self.active_pane.is_archive_view():
			return
		dialog = SortDialog(self.active_pane.sort_mode(), self)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		mode = dialog.result_mode()
		order = dialog.result_order()
		if mode is not None and order is not None:
			self.active_pane.set_sort(mode, order)

	def execute_associated(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			path = self.focused_path()
			if path is None:
				return
			paths = [path]

		try:
			for path in paths:
				open_with_association(path)
		except OSError as error:
			QMessageBox.critical(
				self,
				"関連付け実行エラー",
				f"項目を開けませんでした:\n{error}",
			)

	def open_archive(self, archive_path: Path) -> None:
		password: str | None = None
		while True:
			try:
				self.active_pane.open_archive(
					archive_path,
					password=password,
				)
				return
			except ArchivePasswordError:
				password, ok = QInputDialog.getText(
					self,
					"アーカイブのパスワード",
					f"{archive_path.name} のパスワード:",
					QLineEdit.EchoMode.Password,
				)
				if not ok:
					return
			except Exception as error:
				QMessageBox.critical(
					self,
					"アーカイブを開けません",
					str(error),
				)
				return

	def enter_current_item(self) -> bool:
		if self.active_pane.is_archive_view():
			entry = self.active_pane.focused_entry()
			if entry is None or not entry.is_dir:
				return False
			try:
				self.active_pane.enter_archive_directory(entry)
			except Exception as error:
				QMessageBox.critical(
					self,
					"アーカイブ内を開けません",
					str(error),
				)
			return True

		path = self.focused_path()
		if path is None:
			return False
		if path.is_dir():
			self.active_pane.navigate_to(path)
			return True
		elif is_archive_path(path):
			self.open_archive(path)
			return True
		elif path.suffix.casefold() in IMAGE_EXTENSIONS:
			self._open_image_viewer(path)
			return True
		elif path.suffix.casefold() in MARKDOWN_EXTENSIONS:
			self._open_markdown_viewer(path)
			return True
		elif path.suffix.casefold() in HTML_EXTENSIONS:
			self._open_html_viewer(path)
			return True
		elif path.suffix.casefold() in PDF_EXTENSIONS:
			self._open_pdf_viewer(path)
			return True
		elif path.suffix.casefold() in CSV_EXTENSIONS:
			self._open_csv_viewer(path)
			return True
		elif path.suffix.casefold() in JSON_EXTENSIONS:
			self._open_json_viewer(path)
			return True
		elif path.suffix.casefold() in XML_EXTENSIONS:
			self._open_xml_viewer(path)
			return True
		elif path.suffix.casefold() in LOG_EXTENSIONS:
			self._open_log_viewer(path)
			return True
		elif path.suffix.casefold() in SVG_EXTENSIONS:
			self._open_svg_viewer(path)
			return True
		elif path.suffix.casefold() in PLAINTEXT_EXTENSIONS:
			self._open_plaintext_viewer(path)
			return True
		elif path.suffix.casefold() in HEX_EXTENSIONS:
			self._open_hex_viewer(path)
			return True
		return False

	def _open_image_viewer(self, path: Path) -> None:
		try:
			parent = path.parent
			images = sorted(
				[p for p in parent.iterdir() if p.is_file() and p.suffix.casefold() in IMAGE_EXTENSIONS],
				key=lambda p: p.name.casefold(),
			)
			if not images:
				return
			try:
				index = next(i for i, p in enumerate(images) if p.name.casefold() == path.name.casefold())
			except StopIteration:
				index = 0
			dialog = ImageViewerDialog(images, index, self)
			dialog.exec()
		except Exception as error:
			QMessageBox.critical(self, "画像ビューア エラー", str(error))

	def _open_markdown_viewer(self, path: Path) -> None:
		try:
			text, encoding = read_text_preview(path)
		except ValueError as error:
			QMessageBox.warning(self, "Markdown Viewer", str(error))
			return
		except OSError as error:
			QMessageBox.critical(self, "Markdown Viewer", f"読み込みに失敗しました：\n{error}")
			return
		dialog = MarkdownViewerDialog(path, text, encoding, self)
		dialog.exec()

	def _open_html_viewer(self, path: Path) -> None:
		HtmlViewerDialog(path, self).exec()

	def _open_pdf_viewer(self, path: Path) -> None:
		try:
			PdfViewerDialog(path, self).exec()
		except Exception as error:
			QMessageBox.critical(self, "PDF Viewer", f"PDF を開けませんでした：\n{error}")

	def _open_csv_viewer(self, path: Path) -> None:
		CsvViewerDialog(path, self).exec()

	def _open_json_viewer(self, path: Path) -> None:
		JsonViewerDialog(path, self).exec()

	def _open_xml_viewer(self, path: Path) -> None:
		XmlViewerDialog(path, self).exec()

	def _open_log_viewer(self, path: Path) -> None:
		LogViewerDialog(path, self).exec()

	def _open_svg_viewer(self, path: Path) -> None:
		try:
			SvgViewerDialog(path, self).exec()
		except Exception as error:
			QMessageBox.critical(self, "SVG Viewer", f"SVG を開けませんでした：\n{error}")

	def _open_plaintext_viewer(self, path: Path) -> None:
		try:
			text, encoding = read_text_preview(path)
		except ValueError as error:
			QMessageBox.warning(self, "テキスト Viewer", str(error))
			return
		except OSError as error:
			QMessageBox.critical(self, "テキスト Viewer", f"読み込みに失敗しました：\n{error}")
			return
		TextViewerDialog(path, text, encoding, self).exec()

	def _open_hex_viewer(self, path: Path) -> None:
		HexViewerDialog(path, self).exec()

	def create_archive(self) -> None:
		if self._archive_thread is not None and self._archive_thread.isRunning():
			return

		active_pane = self.active_pane
		inactive_pane = self.inactive_pane
		paths = active_pane.selected_paths()
		focused_path = self.focused_path()
		if not paths and focused_path is not None:
			paths = [focused_path]
		if not paths:
			return

		default_name = "archive.zip"
		if focused_path is not None:
			default_name = f"{focused_path.stem}.zip"

		dialog = ArchiveDialog(inactive_pane.current_path, default_name, self)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		archive_name, password = dialog.values()

		archive_path = (inactive_pane.current_path / archive_name).resolve()
		if archive_path.exists() and archive_path.is_dir():
			QMessageBox.warning(
				self,
				"アーカイブ作成",
				f"保存先に同名のフォルダーがあります:\n{archive_path}",
			)
			return

		overwrite = False
		if archive_path.exists():
			confirm = QMessageBox.question(
				self,
				"上書き確認",
				f"次のアーカイブを上書きしますか？\n\n{archive_path}",
				QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
				QMessageBox.StandardButton.No,
			)
			if confirm != QMessageBox.StandardButton.Yes:
				return
			overwrite = True

		self.statusBar().showMessage(f"アーカイブを作成しています ({len(paths)}件)...")
		thread = ArchiveThread(
			archive_path,
			active_pane.current_path,
			paths,
			overwrite,
			password,
			self,
		)
		thread.completed.connect(self._archive_completed)
		thread.failed.connect(self._archive_failed)
		thread.finished.connect(self._archive_thread_finished)
		self._archive_thread = thread
		thread.start()

	def _archive_completed(self, archive_path: str) -> None:
		self.left_pane.reload()
		self.right_pane.reload()
		self.statusBar().showMessage(
			f"アーカイブを作成しました: {archive_path}",
			5000,
		)

	def _archive_failed(self, message: str) -> None:
		self.statusBar().clearMessage()
		QMessageBox.critical(
			self,
			"アーカイブ作成エラー",
			message,
		)

	def _archive_thread_finished(self) -> None:
		thread = self.sender()
		if thread is self._archive_thread:
			self._archive_thread = None
		if isinstance(thread, QThread):
			thread.deleteLater()

	def extract_archives(self) -> None:
		if self._archive_thread is not None and self._archive_thread.isRunning():
			return

		active_pane = self.active_pane
		inactive_pane = self.inactive_pane
		paths = active_pane.selected_paths()
		if not paths:
			focused_path = self.focused_path()
			if focused_path is None:
				return
			paths = [focused_path]

		confirm = QMessageBox.question(
			self,
			"アーカイブ展開の確認",
			f"選択した項目を次のフォルダーへ展開しますか？\n\n"
			f"{inactive_pane.current_path}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return

		total = len(paths)
		self.statusBar().showMessage("アーカイブを展開しています...")
		thread = ExtractArchiveThread(paths, inactive_pane.current_path, self)
		if total > 1:
			thread.progress.connect(
				lambda done, t: self.statusBar().showMessage(f"アーカイブを展開しています... {done}/{t}件")
			)
		thread.completed.connect(self._extract_completed)
		thread.failed.connect(self._extract_failed)
		thread.finished.connect(self._archive_thread_finished)
		self._archive_thread = thread
		thread.start()

	def _extract_completed(self, extracted_count: int, errors: str) -> None:
		self.left_pane.reload()
		self.right_pane.reload()
		message = f"アーカイブを{extracted_count}件展開しました。"
		if errors:
			message += f" 展開できない項目: {errors}"
		self.statusBar().showMessage(message, 7000)

	def _extract_failed(self, message: str) -> None:
		self.left_pane.reload()
		self.right_pane.reload()
		self._archive_failed(message)

	def show_filter(self) -> None:
		pane = self.active_pane
		dialog = FilterDialog(pane.filter_text(), self)
		dialog.filter_changed.connect(pane.set_filter)
		dialog.move_near(self.inactive_pane)

		if dialog.exec() != QDialog.DialogCode.Accepted:
			pane.clear_filter()
		else:
			pane.set_filter(dialog.filter_text())
		self._update_active_pane()

	def select_drive(self) -> None:
		drives = [
			drive.absoluteFilePath()
			for drive in QDir.drives()
			if drive.isDir()
		]
		if not drives:
			return

		current_drive = self.active_pane.current_path.drive.casefold()
		current_index = next(
			(
				index
				for index, drive in enumerate(drives)
				if Path(drive).drive.casefold() == current_drive
			),
			0,
		)
		selected, ok = QInputDialog.getItem(
			self,
			"ドライブ選択",
			"移動先のドライブ:",
			drives,
			current_index,
			False,
		)
		if ok and selected:
			self.active_pane.navigate_to(selected)

	def confirm_overwrite(self, paths: list[Path], destination: Path) -> bool:
		existing = [destination / path.name for path in paths
					if (destination / path.name).exists()]
		if not existing:
			return True

		return confirm_list(
			self,
			"上書き確認",
			"次の項目は既に存在します。上書きしますか？",
			[path.name for path in existing],
		)

	def _confirm_overwrite_structured(
		self, paths: list[Path], base_path: Path, destination: Path
	) -> bool:
		existing = []
		for path in paths:
			try:
				relative = path.relative_to(base_path)
			except ValueError:
				relative = Path(path.name)
			target = destination / relative
			if target.exists():
				existing.append(target)
		if not existing:
			return True

		return confirm_list(
			self,
			"上書き確認",
			"次の項目は既に存在します。上書きしますか？",
			[str(p.relative_to(destination)) for p in existing],
		)

	def copy_selected(self) -> None:
		if self.active_pane.is_archive_view():
			self.copy_selected_archive_entries()
			return
		if self.inactive_pane.is_archive_view():
			QMessageBox.warning(
				self,
				"ファイルコピー",
				"アーカイブ内をコピー先には指定できません。",
			)
			return

		source = self.active_pane.selected_paths()
		if not source:
			return

		destination = self.inactive_pane.current_path
		if not confirm_list(
			self,
			"ファイルコピー確認",
			f"次の項目をコピーしますか？\n\n{destination}",
			[path.name for path in source],
		):
			return

		use_structure = False
		if self.active_pane.has_search_results() or self.active_pane.has_filter():
			structure_confirm = QMessageBox.question(
				self,
				"フォルダ構成の維持",
				"フォルダ構成を維持しますか？",
				QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			)
			use_structure = structure_confirm == QMessageBox.StandardButton.Yes

		if use_structure:
			base_path = self.active_pane.current_path
			if not self._confirm_overwrite_structured(source, base_path, destination):
				return
			self._start_file_operation(
				lambda cb, s=source, b=base_path, d=destination:
					copy_paths_with_structure(s, b, d, overwrite=True, progress_cb=cb),
				"コピーしています...",
				f"{len(source)}件をコピーしました。",
				total=len(source),
			)
		else:
			if not self.confirm_overwrite(source, destination):
				return
			self._start_file_operation(
				lambda cb, s=source, d=destination:
					copy_paths(s, d, overwrite=True, progress_cb=cb),
				"コピーしています...",
				f"{len(source)}件をコピーしました。",
				total=len(source),
			)

	def copy_selected_archive_entries(self) -> None:
		if self._archive_thread is not None and self._archive_thread.isRunning():
			return
		if self.inactive_pane.is_archive_view():
			QMessageBox.warning(
				self,
				"アーカイブ内コピー",
				"アーカイブ内をコピー先には指定できません。",
			)
			return

		entries = self.active_pane.selected_entries()
		archive_path = self.active_pane.archive_path()
		if not entries or archive_path is None:
			return

		destination = self.inactive_pane.current_path
		if not confirm_list(
			self,
			"アーカイブ内ファイルのコピー確認",
			f"次の項目をコピーしますか？\n\n{destination}",
			[entry.name for entry in entries],
		):
			return

		existing = [
			destination / entry.name
			for entry in entries
			if (destination / entry.name).exists()
		]
		if existing:
			if not confirm_list(
				self,
				"上書き確認",
				"次の項目は既に存在します。上書きしますか？",
				[path.name for path in existing],
			):
				return

		source_pane = self.active_pane
		destination_pane = self.inactive_pane
		self.statusBar().showMessage("アーカイブ内の項目をコピーしています...")
		thread = ArchiveCopyThread(
			archive_path,
			entries,
			destination,
			self.active_pane.archive_password(),
			self,
		)
		thread.completed.connect(
			lambda copied_count, source=source_pane, destination_pane=destination_pane:
				self._archive_copy_completed(
					copied_count,
					source,
					destination_pane,
				)
		)
		thread.failed.connect(self._archive_copy_failed)
		thread.finished.connect(self._archive_thread_finished)
		self._archive_thread = thread
		thread.start()

	def _archive_copy_completed(
		self,
		copied_count: int,
		source_pane: PaneWidget,
		destination_pane: PaneWidget,
	) -> None:
		source_pane.file_view.selectionModel().clearSelection()
		destination_pane.reload()
		self.statusBar().showMessage(
			f"アーカイブ内の項目を{copied_count}件コピーしました。",
			5000,
		)

	def _archive_copy_failed(self, message: str) -> None:
		self.statusBar().clearMessage()
		QMessageBox.critical(
			self,
			"アーカイブ内コピーエラー",
			message,
		)
		
	def create_folder(self) -> None:
		folder_name, ok = QInputDialog.getText(
			self,
			"新規フォルダの作成",
			"フォルダ名:"
		)
		if ok and folder_name:
			new_folder_path = self.active_pane.current_path / folder_name
			if new_folder_path.exists():
				QMessageBox.warning(
					self,
					"警告",
					"同名のフォルダ、またはファイルが存在します。"
				)
				return
			try:
				create_directory(self.active_pane.current_path, folder_name)
			except Exception as e:
				QMessageBox.critical(
					self,
					"エラー",
					f"フォルダの作成に失敗しました:\n{e}"
				)
				return
			self.active_pane.reload()

	def rename_item(self) -> None:
		index = self.active_pane.file_view.currentIndex()
		old_path = self.active_pane.path_from_index(index)
		if old_path is None:
			return

		new_name, ok = QInputDialog.getText(
			self,
			"名前の変更",
			"新しい名前:",
			text=old_path.name
		)
		if ok and new_name:
			new_path = old_path.parent / new_name
			if new_path.exists():
				QMessageBox.warning(
					self,
					"警告",
					"同名のフォルダ、またはファイルが存在します。"
				)
				return
			try:
				rename_path(old_path, new_name)
			except Exception as e:
				QMessageBox.critical(
					self,
					"エラー",
					f"名前の変更に失敗しました:\n{e}"
				)
				return
			self.active_pane.reload()

	def delete_selected(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return

		if not confirm_list(
			self,
			"削除確認",
			"次の項目を完全削除しますか？",
			[path.name for path in paths],
		):
			return

		self._start_file_operation(
			lambda cb, p=paths: delete_paths(p, progress_cb=cb),
			"削除しています...",
			f"{len(paths)}件を削除しました。",
			total=len(paths),
		)

	def trash_selected(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return

		if not confirm_list(
			self,
			"ごみ箱への移動",
			"次の項目をごみ箱へ移動しますか？",
			[path.name for path in paths],
		):
			return

		self._start_file_operation(
			lambda cb, p=paths: trash_paths(p, progress_cb=cb),
			"ごみ箱へ移動しています...",
			f"{len(paths)}件をごみ箱へ移動しました。",
			total=len(paths),
		)

	def move_selected(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return

		destination = self.inactive_pane.current_path
		if not confirm_list(
			self,
			"ファイル移動確認",
			f"次の項目を移動しますか？\n\n{destination}",
			[path.name for path in paths],
		):
			return

		if not self.confirm_overwrite(paths, destination):
			return

		self._start_file_operation(
			lambda cb, p=paths, d=destination: move_paths(p, d, overwrite=True, progress_cb=cb),
			"移動しています...",
			f"{len(paths)}件を移動しました。",
			total=len(paths),
		)

	def _start_file_operation(
		self,
		func: Callable,
		busy_message: str,
		done_message: str,
		total: int = 0,
	) -> None:
		if (self._file_op_thread is not None and self._file_op_thread.isRunning()) or \
		   (self._archive_thread is not None and self._archive_thread.isRunning()):
			return
		self.statusBar().showMessage(busy_message)
		thread = FileOperationThread(func, self)
		if total > 0:
			thread.progress.connect(
				lambda done, t, msg=busy_message:
					self.statusBar().showMessage(f"{msg.rstrip('...')} {done}/{t}件...")
			)
		thread.completed.connect(lambda msg=done_message: self._file_op_completed(msg))
		thread.failed.connect(self._file_op_failed)
		thread.finished.connect(self._file_op_thread_finished)
		self._file_op_thread = thread
		thread.start()

	def _file_op_completed(self, message: str) -> None:
		self.left_pane.reload()
		self.right_pane.reload()
		self.statusBar().showMessage(message, 5000)

	def _file_op_failed(self, message: str) -> None:
		self.left_pane.reload()
		self.right_pane.reload()
		self.statusBar().clearMessage()
		QMessageBox.critical(self, "操作エラー", message)

	def _file_op_thread_finished(self) -> None:
		thread = self.sender()
		if thread is self._file_op_thread:
			self._file_op_thread = None
		if isinstance(thread, QThread):
			thread.deleteLater()

	def eventFilter(self, watched, event) -> bool:
		if event.type() == QEvent.Type.KeyPress:
			km = self._keymap
			_mods = event.modifiers()
			_key = event.key()
			_arrow = _key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right)

			# ── ウィンドウ操作（キーマップ外の固定ショートカット）──
			if _arrow and _mods == (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
				self._resize_window(_key)
				return True
			if _arrow and _mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
				self._move_window(_key)
				return True
			if _mods == Qt.KeyboardModifier.AltModifier and _key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
				self._move_splitter(-1 if _key == Qt.Key.Key_Left else 1)
				return True

			# ── ペイン切り替え（Tab / 左右矢印）──
			no_mod = _mods == Qt.KeyboardModifier.NoModifier
			if (event.key() == Qt.Key.Key_Tab) or \
				(event.key() == Qt.Key.Key_Right and self.active_pane_index == 0 and no_mod) or \
				(event.key() == Qt.Key.Key_Left and self.active_pane_index == 1 and no_mod):
				self._switch_pane()
				return True

			# ── 親へ（左矢印でペイン端）──
			if (event.key() == Qt.Key.Key_Left and self.active_pane_index == 0 and no_mod) or \
				(event.key() == Qt.Key.Key_Right and self.active_pane_index == 1 and no_mod):
				self.active_pane.go_to_parent()
				return True

			# ── Enter / Return ──
			if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
				if km.matches(event, "execute"):
					self.execute_associated()
					return True
				if km.matches(event, "enter"):
					return self.enter_current_item()

			# ── 先頭・末尾へ ──
			if km.matches(event, "go_last"):
				self.active_pane.go_to_last_item()
				return True
			if km.matches(event, "go_first"):
				self.active_pane.go_to_first_item()
				return True

			# ── キーマップ経由ディスパッチ ──
			action = km.action_for(event)

			if action == "center_splitter":
				self._center_splitter()
				return True
			elif action == "copy":
				self.copy_selected()
				return True
			elif action == "copy_names":
				self._copy_names_to_clipboard()
				return True
			elif action == "copy_paths":
				self._copy_paths_to_clipboard()
				return True
			elif action == "move":
				if self.active_pane.selected_paths():
					self.move_selected()
				else:
					self.create_folder()
				return True
			elif action == "delete":
				self.delete_selected()
				return True
			elif action == "trash":
				self.trash_selected()
				return True
			elif action == "rename":
				self.rename_item()
				return True
			elif action == "bulk_rename":
				self.bulk_rename()
				return True
			elif action == "select_down":
				self.active_pane.select_current_and_move_down()
				return True
			elif action == "select_up":
				self.active_pane.select_current_and_move_up()
				return True
			elif action == "page_down":
				self.active_pane.move_page_down()
				return True
			elif action == "page_up":
				self.active_pane.move_page_up()
				return True
			elif action == "range_select":
				self.active_pane.range_select()
				return True
			elif action == "select_all_files":
				self.active_pane.select_all_files()
				return True
			elif action == "select_all":
				self.active_pane.select_all_items()
				return True
			elif action == "deselect":
				self.active_pane.deselect_all()
				return True
			elif action == "reload":
				self.active_pane.reload()
				return True
			elif action == "refresh":
				self.active_pane.reload()
				return True
			elif action == "parent":
				self.active_pane.go_to_parent()
				return True
			elif action == "context_menu":
				self._show_context_menu()
				return True
			elif action == "file_info":
				self._show_file_info()
				return True
			elif action == "folder_size":
				self.calc_folder_size()
				return True
			elif action == "view_text":
				self.view_text()
				return True
			elif action == "open_editor":
				self.open_editor()
				return True
			elif action == "set_editor":
				self.choose_editor()
				return True
			elif action == "terminal":
				self.open_terminal_here()
				return True
			elif action == "compare":
				self.compare_select()
				return True
			elif action == "wildcard":
				self.wildcard_select()
				return True
			elif action == "history":
				self.show_history()
				return True
			elif action == "bookmark":
				self._show_bookmark_list(local=True)
				return True
			elif action == "bookmark_all":
				self._show_bookmark_list(local=False)
				return True
			elif action == "bookmark_add":
				self._toggle_bookmark()
				return True
			elif action == "filter":
				self.show_filter()
				return True
			elif action == "search":
				self.search_files()
				return True
			elif action == "grep":
				self.grep_files()
				return True
			elif action == "sort":
				self.sort_files()
				return True
			elif action == "drive":
				self.select_drive()
				return True
			elif action == "archive":
				self.create_archive()
				return True
			elif action == "extract":
				self.extract_archives()
				return True
			elif action == "sync_panes":
				self.move_to_inactive_path()
				return True
			elif action == "sync_other":
				self.move_inactive_to_active_path()
				return True
			elif action == "font_larger":
				for pane in self.panes:
					pane.adjust_font_size(+1)
				return True
			elif action == "font_smaller":
				for pane in self.panes:
					pane.adjust_font_size(-1)
				return True
			elif action == "home_dir":
				self.active_pane.navigate_to(Path.home())
				return True
			elif action == "jump_path":
				self._jump_to_input_path()
				return True
			elif action == "expand_tree":
				index = self.active_pane.file_view.currentIndex()
				if index.isValid() and self.active_pane.is_dir(index):
					fv = self.active_pane.file_view
					if fv.isExpanded(index):
						fv.collapse(index)
					else:
						fv.expand(index)
				return True
			elif action == "new_file":
				self.create_new_file()
				return True
			elif action == "explorer":
				self.reveal_in_explorer_here()
				return True
			elif action == "toggle_hidden":
				self.toggle_hidden()
				return True
			elif action == "diff":
				self.diff_files()
				return True
			elif action == "git_menu":
				self.show_git_menu()
				return True
			elif action == "keymap_help":
				self.show_keymap_help()
				return True
			elif action == "update_check":
				from ore_filer.version import __version__
				self.statusBar().showMessage(f"Ore Filer v{__version__} — アップデートを確認中...")
				self._check_for_updates(force=True)
				return True
			elif action == "quit":
				confirm = QMessageBox.question(
					self, "終了確認", "アプリケーションを終了しますか？",
					QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
					QMessageBox.StandardButton.No,
				)
				if confirm == QMessageBox.StandardButton.Yes:
					self.close()
				return True

			# ── Escape（アクション名なし）──
			if event.key() == Qt.Key.Key_Escape and no_mod:
				if self.active_pane.has_search_results():
					self.active_pane.clear_search_results()
					return True
				if self.active_pane.has_filter():
					self.active_pane.clear_filter()
					return True

		return super().eventFilter(watched, event)

	_RESIZE_STEP = 50
	_SPLITTER_STEP = 50

	def _move_splitter(self, direction: int) -> None:
		sizes = self.splitter.sizes()
		if len(sizes) != 2:
			return
		total = sizes[0] + sizes[1]
		left = max(0, min(total, sizes[0] + direction * self._SPLITTER_STEP))
		self.splitter.setSizes([left, total - left])

	def _center_splitter(self) -> None:
		sizes = self.splitter.sizes()
		if len(sizes) != 2:
			return
		total = sizes[0] + sizes[1]
		half = total // 2
		self.splitter.setSizes([half, total - half])

	def _move_window(self, key: Qt.Key) -> None:
		step = self._RESIZE_STEP
		pos = self.pos()
		x, y = pos.x(), pos.y()
		if key == Qt.Key.Key_Left:
			x -= step
		elif key == Qt.Key.Key_Right:
			x += step
		elif key == Qt.Key.Key_Up:
			y -= step
		elif key == Qt.Key.Key_Down:
			y += step
		self.move(x, y)

	def _resize_window(self, key: Qt.Key) -> None:
		geo = self.geometry()
		x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
		min_w = max(self.minimumWidth(), 200)
		min_h = max(self.minimumHeight(), 150)
		step = self._RESIZE_STEP
		if key == Qt.Key.Key_Right:
			w = max(min_w, w + step)
		elif key == Qt.Key.Key_Left:
			w = max(min_w, w - step)
		elif key == Qt.Key.Key_Down:
			h = max(min_h, h + step)
		elif key == Qt.Key.Key_Up:
			h = max(min_h, h - step)
		self.setGeometry(x, y, w, h)

	def _clipboard_paths(self) -> list[Path]:
		paths = self.active_pane.selected_paths()
		if not paths:
			focused = self.focused_path()
			if focused:
				paths = [focused]
		return paths

	def _copy_names_to_clipboard(self) -> None:
		paths = self._clipboard_paths()
		if paths:
			QGuiApplication.clipboard().setText("\n".join(p.name for p in paths))

	def _copy_paths_to_clipboard(self) -> None:
		paths = self._clipboard_paths()
		if paths:
			QGuiApplication.clipboard().setText("\n".join(str(p) for p in paths))

	def _switch_pane(self) -> None:
		self.active_pane_index = 1 - self.active_pane_index
		self._update_active_pane()

	def _update_active_pane(self) -> None:
		active_pane = self.active_pane
		for pane in self.panes:
			pane.setStyleSheet("")
			pane.file_view.set_cursor_visible(pane is active_pane)
			pane.file_view.viewport().update()
		active_pane.setStyleSheet("border: 1px solid #4b6584;")
		active_pane.file_view.setFocus()
		self._update_status_bar()

	def _update_status_bar(self) -> None:
		pane = self.active_pane
		sel = pane.selected_count()
		if sel > 0:
			size = pane.selected_total_size()
			self.statusBar().showMessage(f"選択: {sel}件 / {_fmt_size(size)}")
		else:
			n = pane.visible_item_count()
			self.statusBar().showMessage(f"{n}件")

	def _show_context_menu(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			path = self.focused_path()
			if path is None:
				return
			paths = [path]
		show_shell_context_menu(paths, int(self.winId()))

	def _show_file_info(self) -> None:
		path = self.focused_path()
		if path is None:
			return
		FileInfoDialog(path, self).exec()

	def _on_files_dropped(
		self, raw_paths: list[str], destination: str, is_move: bool
	) -> None:
		from pathlib import Path as _Path

		dest = _Path(destination).resolve()
		sources: list[Path] = []
		for raw in raw_paths:
			src = _Path(raw).resolve()
			if not src.exists():
				continue
			# 同フォルダへのドロップは何もしない（copy_paths が元を消す危険を避ける）
			if src.parent == dest:
				continue
			# フォルダをそれ自身の中へはドロップ不可
			if src.is_dir():
				try:
					dest.relative_to(src)
					continue  # dest が src の配下
				except ValueError:
					pass
			sources.append(src)

		if not sources:
			return

		# ドロップ先ペインをアクティブにする
		for i, pane in enumerate(self.panes):
			if _Path(str(pane.current_path)).resolve() == dest or str(pane.current_path) == destination:
				self.active_pane_index = i
				self._update_active_pane()
				break

		op = "移動" if is_move else "コピー"
		if not confirm_list(
			self,
			f"ドロップ {op} 確認",
			f"次の項目を {op} しますか？\n\n{dest}",
			[src.name for src in sources],
		):
			return

		if not self.confirm_overwrite(sources, dest):
			return

		if is_move:
			self._start_file_operation(
				lambda cb, s=sources, d=dest: move_paths(s, d, overwrite=True, progress_cb=cb),
				"移動しています...",
				f"{len(sources)}件を移動しました。",
				total=len(sources),
			)
		else:
			self._start_file_operation(
				lambda cb, s=sources, d=dest: copy_paths(s, d, overwrite=True, progress_cb=cb),
				"コピーしています...",
				f"{len(sources)}件をコピーしました。",
				total=len(sources),
			)

	# ── テキストビューア ──────────────────────────────────────

	def view_text(self) -> None:
		path = self.focused_path()
		if path is None or path.is_dir() or self.active_pane.is_archive_view():
			return
		try:
			text, encoding = read_text_preview(path)
		except ValueError as e:
			QMessageBox.warning(self, "テキストビューア", str(e))
			return
		except OSError as e:
			QMessageBox.warning(self, "テキストビューア", f"読み込みに失敗しました:\n{e}")
			return
		lines = self.active_pane.grep_lines(path)
		TextViewerDialog(path, text, encoding, highlight_lines=lines or None, parent=self).exec()

	# ── 外部エディタで開く ────────────────────────────────────

	def open_editor(self) -> None:
		path = self.focused_path()
		if path is None:
			return
		config = load_config()
		editor = config.get("editor", "")
		if not editor:
			editor = self._choose_editor()
			if not editor:
				return
		try:
			open_in_editor(path, editor)
		except OSError as e:
			QMessageBox.warning(self, "外部エディタ", f"エディタを起動できませんでした:\n{e}")

	def _choose_editor(self) -> str:
		from PySide6.QtWidgets import QFileDialog
		exe, _ = QFileDialog.getOpenFileName(
			self,
			"エディタの実行ファイルを選択",
			"C:\\",
			"実行ファイル (*.exe)",
		)
		if exe:
			config = load_config()
			config["editor"] = exe
			save_config(config)
		return exe

	def choose_editor(self) -> None:
		"""設定済みエディタを変更する（Shift+E）。"""
		exe = self._choose_editor()
		if exe:
			self.statusBar().showMessage(f"エディタを設定しました: {exe}", 5000)

	# ── ターミナルを開く ──────────────────────────────────────

	def open_terminal_here(self) -> None:
		try:
			open_terminal(self.active_pane.current_path)
		except OSError as e:
			QMessageBox.warning(self, "ターミナル", f"起動できませんでした:\n{e}")

	# ── 一括リネーム ──────────────────────────────────────────

	def bulk_rename(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return
		dialog = BulkRenameDialog(paths, self)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		pairs = dialog.result_pairs()
		if not pairs:
			self.statusBar().showMessage("変更なし", 3000)
			return
		names_preview = [f"{p.name}  →  {n}" for p, n in pairs]
		if not confirm_list(self, "一括リネーム確認", "次の項目をリネームしますか？", names_preview):
			return
		self._start_file_operation(
			lambda p=pairs: rename_paths(p),
			"リネームしています...",
			f"{len(pairs)}件をリネームしました。",
		)

	# ── 左右ペイン比較選択 ────────────────────────────────────

	def compare_select(self) -> None:
		if self.left_pane.is_archive_view() or self.right_pane.is_archive_view():
			return
		if self.left_pane.has_search_results() or self.right_pane.has_search_results():
			return
		dialog = CompareSelectDialog(
			str(self.left_pane.current_path),
			str(self.right_pane.current_path),
			self,
		)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		mode = dialog.mode()
		if mode is None:
			return

		left_items = {p.name.casefold(): p for p in self.left_pane.current_path.iterdir()
					  if not self.left_pane.current_path == p}
		right_items = {p.name.casefold(): p for p in self.right_pane.current_path.iterdir()
					   if not self.right_pane.current_path == p}

		left_select: list[Path] = []
		right_select: list[Path] = []

		if mode == CompareSelectDialog.ONLY_LEFT:
			left_select = [left_items[k] for k in left_items if k not in right_items]
		elif mode == CompareSelectDialog.ONLY_RIGHT:
			right_select = [right_items[k] for k in right_items if k not in left_items]
		elif mode == CompareSelectDialog.SAME_NAME:
			common = set(left_items) & set(right_items)
			left_select = [left_items[k] for k in common]
			right_select = [right_items[k] for k in common]
		elif mode == CompareSelectDialog.NEWER:
			for k in set(left_items) & set(right_items):
				try:
					lmt = left_items[k].stat().st_mtime
					rmt = right_items[k].stat().st_mtime
					if lmt >= rmt:
						left_select.append(left_items[k])
					else:
						right_select.append(right_items[k])
				except OSError:
					pass
		elif mode == CompareSelectDialog.DIFF_SIZE:
			for k in set(left_items) & set(right_items):
				try:
					if left_items[k].stat().st_size != right_items[k].stat().st_size:
						left_select.append(left_items[k])
						right_select.append(right_items[k])
				except OSError:
					pass

		if left_select:
			self.left_pane.select_paths(left_select)
		if right_select:
			self.right_pane.select_paths(right_select)
		total = len(left_select) + len(right_select)
		self.statusBar().showMessage(f"比較選択: {total}件", 5000)

	# ── フォルダサイズ計算 ────────────────────────────────────

	def calc_folder_size(self) -> None:
		if self._dir_size_thread is not None and self._dir_size_thread.isRunning():
			return
		targets = [p for p in self.active_pane.selected_paths() if p.is_dir()]
		if not targets:
			path = self.focused_path()
			if path is not None and path.is_dir():
				targets = [path]
		if not targets:
			return
		self.statusBar().showMessage("フォルダサイズを計算しています...")
		thread = DirSizeThread(targets, self)
		thread.progress.connect(lambda msg: self.statusBar().showMessage(msg))
		thread.finished_with.connect(self._on_dir_size_finished)
		thread.finished.connect(self._on_dir_size_thread_finished)
		self._dir_size_thread = thread
		thread.start()

	def _on_dir_size_finished(self, results: list) -> None:
		self.statusBar().clearMessage()
		lines = []
		for path, total_bytes, total_files in results:
			lines.append(f"{path.name}\n  {_fmt_size(total_bytes)}  ({total_files:,} ファイル)")
		QMessageBox.information(
			self,
			"フォルダサイズ",
			"\n\n".join(lines),
		)

	def _on_dir_size_thread_finished(self) -> None:
		thread = self.sender()
		if thread is self._dir_size_thread:
			self._dir_size_thread = None
		if isinstance(thread, QThread):
			thread.deleteLater()

	# ── ワイルドカード選択 ────────────────────────────────────

	def wildcard_select(self) -> None:
		if self.active_pane.is_archive_view():
			return
		text, ok = QInputDialog.getText(
			self,
			"ワイルドカード選択",
			"パターン（複数は ; 区切り）:",
			text="*.",
		)
		if not ok or not text.strip():
			return
		patterns = [p for p in text.split(";") if p.strip()]
		if not patterns:
			return
		count = self.active_pane.select_by_pattern(patterns)
		self.statusBar().showMessage(f"ワイルドカード選択: {count}件", 5000)

	# ── GREP ─────────────────────────────────────────────────

	def grep_files(self) -> None:
		if self.active_pane.is_archive_view():
			return
		dialog = GrepDialog(self.active_pane.current_path, self)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		paths = dialog.matched_paths()
		if paths:
			self.active_pane.show_search_results(paths, dialog.line_hits())

	# ── 新規ファイル作成 ─────────────────────────────────────

	def create_new_file(self) -> None:
		name, ok = QInputDialog.getText(self, "新規ファイルの作成", "ファイル名:")
		if not ok or not name:
			return
		new_path = self.active_pane.current_path / name
		if new_path.exists():
			QMessageBox.warning(self, "警告", "同名のファイルまたはフォルダが存在します。")
			return
		try:
			create_file(self.active_pane.current_path, name)
		except Exception as e:
			QMessageBox.critical(self, "エラー", f"ファイルの作成に失敗しました:\n{e}")
			return
		self.active_pane.reload()
		self.active_pane._pending_focus = name

	# ── エクスプローラーで開く ────────────────────────────────

	def reveal_in_explorer_here(self) -> None:
		path = self.focused_path() or self.active_pane.current_path
		try:
			reveal_in_explorer(path)
		except OSError as e:
			QMessageBox.warning(self, "エクスプローラー", f"起動できませんでした:\n{e}")

	# ── 隠しファイル表示切替 ──────────────────────────────────

	def toggle_hidden(self) -> None:
		cfg = load_config()
		show = not cfg.get("show_hidden", True)
		cfg["show_hidden"] = show
		save_config(cfg)
		for pane in self.panes:
			pane.set_show_hidden(show)
		label = "表示" if show else "非表示"
		self.statusBar().showMessage(f"隠しファイル: {label}", 3000)

	# ── 差分表示 ─────────────────────────────────────────────

	def diff_files(self) -> None:
		def _pane_focused(pane: "PaneWidget") -> Path | None:
			idx = pane.file_view.currentIndex()
			return pane.path_from_index(idx)
		left_path = _pane_focused(self.left_pane)
		right_path = _pane_focused(self.right_pane)
		if left_path is None or right_path is None:
			QMessageBox.information(self, "差分表示", "左右ペインでファイルを選択してください。")
			return
		if left_path.is_dir() or right_path.is_dir():
			QMessageBox.information(self, "差分表示", "ファイルを選択してください（フォルダは不可）。")
			return
		try:
			left_text, right_text = read_text_pair(left_path, right_path)
		except ValueError as e:
			QMessageBox.warning(self, "差分表示", str(e))
			return
		except OSError as e:
			QMessageBox.warning(self, "差分表示", f"読み込みに失敗しました:\n{e}")
			return
		SideBySideDiffDialog(left_path.name, right_path.name, left_text, right_text, self).exec()

	# ── Git 操作 ─────────────────────────────────────────────

	def _refresh_git_status(self, pane: PaneWidget) -> None:
		"""バックグラウンドで Git 状態を取得してペインに反映する。"""
		if not git_service.is_available():
			pane.set_git_info(None, {})
			return
		pane_id = id(pane)
		pane_path = pane.current_path
		thread = GitStatusThread(pane_path, self)
		thread.finished_with.connect(
			lambda result_path, branch, status, p=pane, pp=str(pane_path):
				self._on_git_status_ready(p, pp, result_path, branch, status)
		)

		def _cleanup(pid=pane_id, t=thread):
			if self._git_status_threads.get(pid) is t:
				del self._git_status_threads[pid]
			t.deleteLater()

		thread.finished.connect(_cleanup)
		self._git_status_threads[pane_id] = thread
		thread.start()

	def _on_git_status_ready(
		self,
		pane: PaneWidget,
		expected_path: str,
		result_path: str,
		branch,
		status_map: dict,
	) -> None:
		"""GitStatusThread の完了時コールバック。"""
		if str(pane.current_path) != expected_path:
			return  # ペインが既に別のパスへ移動済み
		pane.set_git_info(branch, status_map)

	def _git_repo(self) -> "Path | None":
		"""アクティブペインの Git リポジトリルートを返す。なければ None。"""
		if not git_service.is_available():
			return None
		return git_service.find_repo_root(self.active_pane.current_path)

	def show_git_menu(self) -> None:
		"""G: Git ウィンドウを開く。"""
		from ore_filer.gui.git_window import GitWindow

		if not git_service.is_available():
			QMessageBox.information(
				self,
				"Git",
				"Git機能を利用できません。Dulwichがインストールされていません。",
			)
			return
		repo = git_service.find_repo_root(self.active_pane.current_path)
		if repo is None:
			QMessageBox.information(self, "Git", "Git リポジトリではありません。")
			return

		win = self._git_windows.get(repo)
		if win is None:
			win = GitWindow(repo, self)
			win.repo_changed.connect(self._on_git_repo_changed)
			self._git_windows[repo] = win

		win.show()
		win.raise_()
		win.activateWindow()
		win._refresh()

	def _on_git_repo_changed(self) -> None:
		for pane in self.panes:
			self._refresh_git_status(pane)

	def git_stage(self) -> None:
		"""選択/カーソル項目をステージする。"""
		repo = self._git_repo()
		if repo is None:
			return
		paths = self.active_pane.selected_paths()
		if not paths:
			path = self.focused_path()
			if path is None:
				return
			paths = [path]
		try:
			git_service.stage(repo, paths)
		except Exception as e:
			QMessageBox.warning(self, "Git ステージ", f"ステージに失敗しました:\n{e}")
			return
		self._refresh_git_status(self.active_pane)
		self.statusBar().showMessage(f"{len(paths)} 件をステージしました", 3000)

	def git_unstage(self) -> None:
		"""選択/カーソル項目のステージを解除する。"""
		repo = self._git_repo()
		if repo is None:
			return
		paths = self.active_pane.selected_paths()
		if not paths:
			path = self.focused_path()
			if path is None:
				return
			paths = [path]
		try:
			git_service.unstage(repo, paths)
		except Exception as e:
			QMessageBox.warning(self, "Git ステージ解除", f"ステージ解除に失敗しました:\n{e}")
			return
		self._refresh_git_status(self.active_pane)
		self.statusBar().showMessage(f"{len(paths)} 件のステージを解除しました", 3000)

	def git_commit(self) -> None:
		"""ステージ済みファイルをコミットするダイアログを開く。"""
		repo = self._git_repo()
		if repo is None:
			return
		try:
			raw_map = git_service.status(repo)
		except Exception as e:
			QMessageBox.warning(self, "Git コミット", str(e))
			return

		staged: list[str] = []
		for path, xy in raw_map.items():
			if (
				len(xy) >= 1
				and xy[0] not in (" ", "?", "!")
				and xy != "D~"
				and path.is_file()
			):
				try:
					staged.append(str(path.relative_to(repo)))
				except ValueError:
					staged.append(path.name)

		if not staged:
			QMessageBox.information(self, "Git コミット", "ステージされた変更がありません。")
			return

		dialog = GitCommitDialog(staged, self)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		msg = dialog.commit_message()
		if not msg:
			return

		thread = GitCommandThread(lambda r=repo, m=msg: git_service.commit(r, m), self)
		thread.succeeded.connect(lambda out: self._on_git_command_done("コミット", out))
		thread.failed.connect(lambda err: self._on_git_command_failed("コミット", err))
		thread.finished.connect(thread.deleteLater)
		thread.start()

	def _http_credentials(self, repo) -> tuple[str, str] | None:
		"""HTTPリモートの場合に認証ダイアログを表示してcredentialsを返す。不要なら(None,None)相当。"""
		from ore_filer import credentials as cred_store
		url = git_service.get_remote_url(repo)
		if not (url and url.startswith(("http://", "https://"))):
			return ("", "")
		saved = cred_store.load("git", url)
		username, password = saved if saved else ("", "")
		dlg = GitHttpCredentialsDialog(url, username=username, password=password, parent=self)
		if dlg.exec() != GitHttpCredentialsDialog.DialogCode.Accepted:
			return None
		username, password = dlg.credentials()
		if dlg.should_save():
			cred_store.save("git", url, username, password)
		else:
			cred_store.delete("git", url)
		return username, password

	def git_fetch(self) -> None:
		"""リモートの参照を取得する。作業ツリーは変更しない。"""
		repo = self._git_repo()
		if repo is None:
			return
		confirm = QMessageBox.question(
			self, "git fetch", "リモートから最新の履歴を取得しますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return
		creds = self._http_credentials(repo)
		if creds is None:
			return
		username, password = creds
		self.statusBar().showMessage("git fetch 実行中...")
		thread = GitCommandThread(
			lambda r=repo, u=username, p=password: git_service.fetch(r, username=u or None, password=p or None),
			self,
		)
		thread.succeeded.connect(lambda out: self._on_git_sync_done("fetch", out))
		thread.failed.connect(lambda err: self._on_git_command_failed("fetch", err))
		thread.finished.connect(thread.deleteLater)
		thread.start()

	def git_pull(self) -> None:
		"""git pull を実行する。"""
		repo = self._git_repo()
		if repo is None:
			return
		confirm = QMessageBox.question(
			self, "git pull", "リモートから変更を取得しますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return
		creds = self._http_credentials(repo)
		if creds is None:
			return
		username, password = creds
		self.statusBar().showMessage("git pull 実行中...")
		thread = GitCommandThread(
			lambda r=repo, u=username, p=password: git_service.pull(r, username=u or None, password=p or None),
			self,
		)
		thread.succeeded.connect(lambda out: self._on_git_sync_done("pull", out))
		thread.failed.connect(lambda err: self._on_git_command_failed("pull", err))
		thread.finished.connect(thread.deleteLater)
		thread.start()

	def git_push(self) -> None:
		"""git push を実行する。"""
		repo = self._git_repo()
		if repo is None:
			return
		confirm = QMessageBox.question(
			self, "git push", "リモートへ変更を送信しますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return
		creds = self._http_credentials(repo)
		if creds is None:
			return
		username, password = creds
		self.statusBar().showMessage("git push 実行中...")
		thread = GitCommandThread(
			lambda r=repo, u=username, p=password: git_service.push(r, username=u or None, password=p or None),
			self,
		)
		thread.succeeded.connect(lambda out: self._on_git_sync_done("push", out))
		thread.failed.connect(lambda err: self._on_git_command_failed("push", err))
		thread.finished.connect(thread.deleteLater)
		thread.start()

	def git_diff(self) -> None:
		"""カーソルファイルの git diff HEAD を表示する。"""
		repo = self._git_repo()
		if repo is None:
			return
		path = self.focused_path()
		if path is None or path.is_dir():
			QMessageBox.information(self, "Git 差分", "ファイルを選択してください。")
			return
		try:
			head_text = git_service.head_file_text(repo, path)
			work_text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
		except Exception as e:
			QMessageBox.warning(self, "Git 差分", str(e))
			return
		SideBySideDiffDialog("HEAD", path.name, head_text, work_text, self).exec()

	def git_log(self) -> None:
		"""Git ログを表示し、選択したコミットの差分を表示する。"""
		repo = self._git_repo()
		if repo is None:
			return
		try:
			entries = git_service.log(repo)
		except Exception as e:
			QMessageBox.warning(self, "Git ログ", str(e))
			return
		if not entries:
			QMessageBox.information(self, "Git ログ", "コミット履歴がありません。")
			return

		dialog = GitLogDialog(entries, self)
		if dialog.exec() == QDialog.DialogCode.Accepted:
			commit_hash = dialog.selected_commit()
			if commit_hash:
				try:
					diff_text = git_service.show(repo, commit_hash)
					DiffDialog(commit_hash, "show", diff_text, self).exec()
				except Exception as e:
					QMessageBox.warning(self, "git show", str(e))

	def git_switch_branch(self) -> None:
		"""ブランチ切替ダイアログを開く。"""
		repo = self._git_repo()
		if repo is None:
			return
		try:
			branch_list = git_service.branches(repo)
		except Exception as e:
			QMessageBox.warning(self, "ブランチ切替", str(e))
			return
		if not branch_list:
			QMessageBox.information(self, "ブランチ切替", "ブランチが見つかりません。")
			return

		current = branch_list[0] if branch_list else ""
		selected, ok = QInputDialog.getItem(
			self, "ブランチ切替", "切り替え先のブランチ:", branch_list, 0, False,
		)
		if not ok or selected == current:
			return

		confirm = QMessageBox.question(
			self, "ブランチ切替",
			f"ブランチを '{selected}' に切り替えますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return
		try:
			git_service.switch(repo, selected)
		except Exception as e:
			QMessageBox.warning(self, "ブランチ切替", f"切り替えに失敗しました:\n{e}")
			return
		for pane in self.panes:
			self._refresh_git_status(pane)
		self.statusBar().showMessage(f"ブランチを '{selected}' に切り替えました", 5000)

	def _on_git_command_done(self, op: str, output: str) -> None:
		for pane in self.panes:
			self._refresh_git_status(pane)
		self.statusBar().showMessage(f"Git {op} 完了", 5000)

	def _on_git_sync_done(self, op: str, output: str) -> None:
		for pane in self.panes:
			pane.reload()
			self._refresh_git_status(pane)
		self.statusBar().showMessage(f"git {op} 完了", 5000)
		if output.strip():
			QMessageBox.information(self, f"git {op}", output.strip())

	def _on_git_command_failed(self, op: str, error: str) -> None:
		self.statusBar().clearMessage()
		QMessageBox.warning(self, f"Git {op} エラー", error)

	# ── キーマップウォッチャー ────────────────────────────────

	def _setup_keymap_watcher(self) -> None:
		from PySide6.QtCore import QFileSystemWatcher
		km_path = str(user_keymap_file())
		self._keymap_watcher = QFileSystemWatcher(self)
		self._keymap_watcher.addPath(km_path)
		self._keymap_watcher.fileChanged.connect(self._on_keymap_file_changed)

	def _on_keymap_file_changed(self, path: str) -> None:
		# エディタによってはファイルを削除→作成するため再登録が必要
		if self._keymap_watcher is not None:
			self._keymap_watcher.addPath(path)
		self._keymap.reload()
		self.statusBar().showMessage("キーマップを再読み込みしました", 3000)

	# ── キー一覧 ─────────────────────────────────────────────

	def show_keymap_help(self) -> None:
		bindings = self._keymap.all_bindings()
		KeymapHelpDialog(bindings, self).exec()

	def _jump_to_input_path(self) -> None:
		text, ok = QInputDialog.getText(
			self,
			"パスを入力して移動",
			"移動先のパス:",
			text=str(self.active_pane.current_path),
		)
		if not ok or not text.strip():
			return
		target = Path(text.strip()).expanduser()
		if target.is_dir():
			self.active_pane.navigate_to(target)
		elif target.is_file():
			self.active_pane.navigate_to(target.parent, focus_name=target.name)
		else:
			QMessageBox.warning(self, "移動エラー", f"パスが見つかりません:\n{target}")

	def _check_for_updates(self, force: bool = False) -> None:
		from datetime import datetime, timedelta, timezone

		if not force:
			session = load_session()
			last = session.get("last_update_check", "")
			self._last_update_check = last
			if last:
				try:
					last_dt = datetime.fromisoformat(last)
					if datetime.now(timezone.utc) - last_dt < timedelta(hours=24):
						return
				except ValueError:
					pass

		self._last_update_check = datetime.now(timezone.utc).isoformat()
		self._update_check_thread = UpdateCheckThread()
		self._update_check_thread.update_available.connect(self._on_update_available)
		if force:
			self._update_check_thread.up_to_date.connect(
				lambda: self.statusBar().showMessage("最新版です")
			)
			self._update_check_thread.check_failed.connect(
				lambda: self.statusBar().showMessage("アップデート確認に失敗しました")
			)
		self._update_check_thread.start()

	def _on_update_available(self, tag: str, url: str) -> None:
		answer = QMessageBox.question(
			self,
			"アップデート",
			f"新しいバージョン {tag} があります。ダウンロードしますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
		)
		if answer != QMessageBox.StandardButton.Yes:
			return
		self.statusBar().showMessage("アップデートをダウンロード中...")
		self._update_download_thread = UpdateDownloadThread(url)
		self._update_download_thread.download_progress.connect(
			lambda p: self.statusBar().showMessage(f"ダウンロード中... {p}%")
		)
		self._update_download_thread.download_finished.connect(self._on_download_finished)
		self._update_download_thread.download_failed.connect(
			lambda e: self.statusBar().showMessage(f"ダウンロード失敗: {e}")
		)
		self._update_download_thread.start()

	def _on_download_finished(self, path: str) -> None:
		import sys

		if getattr(sys, "frozen", False):
			install_update(path)
			QMessageBox.information(
				self,
				"アップデート完了",
				"アップデートを適用しました。\nアプリを再起動してください。",
				QMessageBox.StandardButton.Ok,
			)
			self.close()
		else:
			self.statusBar().showMessage("開発モードのためインストールをスキップしました")

