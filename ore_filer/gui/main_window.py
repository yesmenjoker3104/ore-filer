import base64
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QThread, Qt, Signal
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
	ArchiveDialog,
	BookmarkDialog,
	FileInfoDialog,
	FilterDialog,
	HistoryDialog,
	IMAGE_EXTENSIONS,
	ImageViewerDialog,
	SearchDialog,
	SortDialog,
)
from ore_filer.gui.context_menu import show_shell_context_menu
from ore_filer.gui.pane import PaneWidget
from ore_filer.settings import load_bookmarks, save_bookmarks, save_session
from ore_filer.services.file_operations import (
	ArchiveEntry,
	ArchivePasswordError,
	copy_paths,
	copy_paths_with_structure,
	copy_archive_entries,
	create_directory,
	delete_paths,
	trash_paths,
	is_archive_path,
	rename_path,
	move_paths,
	open_with_association,
	create_archive,
	extract_archive,
)


class FileOperationThread(QThread):
	completed = Signal()
	failed = Signal(str)

	def __init__(self, func: Callable[[], None], parent=None):
		super().__init__(parent)
		self._func = func

	def run(self) -> None:
		try:
			self._func()
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
		for archive_path in self.archive_paths:
			try:
				extract_archive(archive_path, self.destination)
			except Exception as error:
				errors.append(f"{archive_path.name}: {error}")
			else:
				extracted_count += 1

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
		self.setWindowTitle("Ore Filer")
		self.resize(1200, 700)
		shared_history = history if history is not None else []
		self.left_pane = PaneWidget(left_path, history=shared_history)
		self.right_pane = PaneWidget(right_path, history=shared_history)

		self.panes = [self.left_pane, self.right_pane]
		self.active_pane_index = 0
		self._archive_thread: QThread | None = None
		self._file_op_thread: FileOperationThread | None = None
		self._pending_g = False

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
		)
		super().closeEvent(event)

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
		import tempfile, os
		with open(os.path.join(tempfile.gettempdir(), "ore_filer_debug.txt"), "a") as _f:
			_f.write(f"enter: path={path} suffix={path.suffix.casefold()!r} in_ext={path.suffix.casefold() in IMAGE_EXTENSIONS}\n")
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

		self.statusBar().showMessage("アーカイブを作成しています...")
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

		self.statusBar().showMessage("アーカイブを展開しています...")
		thread = ExtractArchiveThread(paths, inactive_pane.current_path, self)
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

		names = "\n".join(path.name for path in existing)
		confirm = QMessageBox.question(
			self,
			"上書き確認",
			f"次の項目は既に存在します。上書きしますか？\n\n{names}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		return confirm == QMessageBox.StandardButton.Yes

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

		names = "\n".join(str(p.relative_to(destination)) for p in existing)
		confirm = QMessageBox.question(
			self,
			"上書き確認",
			f"次の項目は既に存在します。上書きしますか？\n\n{names}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		return confirm == QMessageBox.StandardButton.Yes

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
		names = "\n".join(path.name for path in source)

		confirm = QMessageBox.question(
			self,
			"ファイルコピー確認",
			f"次の項目をコピーしますか？\n\n{destination}\n\n{names}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
		)

		if confirm != QMessageBox.StandardButton.Yes:
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
				lambda s=source, b=base_path, d=destination:
					copy_paths_with_structure(s, b, d, overwrite=True),
				"コピーしています...",
				f"{len(source)}件をコピーしました。",
			)
		else:
			if not self.confirm_overwrite(source, destination):
				return
			self._start_file_operation(
				lambda s=source, d=destination:
					copy_paths(s, d, overwrite=True),
				"コピーしています...",
				f"{len(source)}件をコピーしました。",
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
		names = "\n".join(entry.name for entry in entries)
		confirm = QMessageBox.question(
			self,
			"アーカイブ内ファイルのコピー確認",
			f"次の項目をコピーしますか？\n\n{destination}\n\n{names}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return

		existing = [
			destination / entry.name
			for entry in entries
			if (destination / entry.name).exists()
		]
		if existing:
			existing_names = "\n".join(path.name for path in existing)
			confirm = QMessageBox.question(
				self,
				"上書き確認",
				f"次の項目は既に存在します。上書きしますか？\n\n{existing_names}",
				QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
				QMessageBox.StandardButton.No,
			)
			if confirm != QMessageBox.StandardButton.Yes:
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

		confirm = QMessageBox.question(
			self,
			"削除確認",
			f"次の項目を完全削除しますか？\n\n{chr(10).join(path.name for path in paths)}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return

		self._start_file_operation(
			lambda p=paths: delete_paths(p),
			"削除しています...",
			f"{len(paths)}件を削除しました。",
		)

	def trash_selected(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return

		confirm = QMessageBox.question(
			self,
			"ごみ箱への移動",
			f"次の項目をごみ箱へ移動しますか？\n\n{chr(10).join(path.name for path in paths)}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return

		self._start_file_operation(
			lambda p=paths: trash_paths(p),
			"ごみ箱へ移動しています...",
			f"{len(paths)}件をごみ箱へ移動しました。",
		)

	def move_selected(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return

		destination = self.inactive_pane.current_path
		names = "\n".join(path.name for path in paths)
		confirm = QMessageBox.question(
			self,
			"ファイル移動確認",
			f"次の項目を移動しますか？\n\n{destination}\n\n{names}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return

		if not self.confirm_overwrite(paths, destination):
			return

		self._start_file_operation(
			lambda p=paths, d=destination: move_paths(p, d, overwrite=True),
			"移動しています...",
			f"{len(paths)}件を移動しました。",
		)

	def _start_file_operation(
		self,
		func: Callable[[], None],
		busy_message: str,
		done_message: str,
	) -> None:
		if (self._file_op_thread is not None and self._file_op_thread.isRunning()) or \
		   (self._archive_thread is not None and self._archive_thread.isRunning()):
			return
		self.statusBar().showMessage(busy_message)
		thread = FileOperationThread(func, self)
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
			_mods = event.modifiers()
			_key = event.key()
			_arrow = _key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right)
			if _arrow and _mods == (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier):
				self._resize_window(_key)
				return True
			if _arrow and _mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
				self._move_window(_key)
				return True
			if _mods == Qt.KeyboardModifier.AltModifier and _key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
				self._move_splitter(-1 if _key == Qt.Key.Key_Left else 1)
				return True
			if event.text() == "=":
				self._center_splitter()
				return True
			no_mod = _mods == Qt.KeyboardModifier.NoModifier
			if (event.key() == Qt.Key.Key_Tab) or \
				(event.key() == Qt.Key.Key_Right and self.active_pane_index == 0 and no_mod) or \
				(event.key() == Qt.Key.Key_Left and self.active_pane_index == 1 and no_mod):
				self._switch_pane()
				return True

			elif event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
				if _mods == Qt.KeyboardModifier.ControlModifier:
					self.execute_associated()
					return True
				if no_mod:
					return self.enter_current_item()

			elif (event.key() == Qt.Key.Key_Left and self.active_pane_index == 0 and no_mod) or \
				(event.key() == Qt.Key.Key_Right and self.active_pane_index == 1 and no_mod):
				self.active_pane.go_to_parent()
				return True
			
			elif event.key() == Qt.Key.Key_T:
				index = self.active_pane.file_view.currentIndex()
				if index.isValid() and self.active_pane.is_dir(index):
					file_view = self.active_pane.file_view

					if file_view.isExpanded(index):
						file_view.collapse(index)
					else:
						file_view.expand(index)

					return True
			elif event.key() == Qt.Key.Key_F5:
				self.active_pane.reload()
				return True
			elif event.key() == Qt.Key.Key_C:
				if _mods == Qt.KeyboardModifier.ControlModifier:
					self._copy_names_to_clipboard()
					return True
				if _mods == (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
					self._copy_paths_to_clipboard()
					return True
				if _mods == Qt.KeyboardModifier.NoModifier:
					self.copy_selected()
					return True
			elif event.key() == Qt.Key.Key_Space:
				if _mods == Qt.KeyboardModifier.ShiftModifier:
					self.active_pane.select_current_and_move_up()
					return True
				if _mods == Qt.KeyboardModifier.ControlModifier:
					self.active_pane.range_select()
					return True
				self.active_pane.select_current_and_move_down()
				return True
			elif event.key() == Qt.Key.Key_Backslash and no_mod:
				self._show_context_menu()
				return True
			elif event.key() == Qt.Key.Key_I and no_mod:
				self._show_file_info()
				return True
			elif event.key() == Qt.Key.Key_End:
				if no_mod:
					self.active_pane.deselect_all()
					return True
				if _mods == Qt.KeyboardModifier.ShiftModifier:
					self.active_pane.reload()
					return True
			elif event.key() == Qt.Key.Key_A and no_mod:
				self.active_pane.select_all_files()
				return True
			elif event.key() == Qt.Key.Key_Home and no_mod:
				self.active_pane.select_all_files()
				return True
			elif event.key() == Qt.Key.Key_A and \
				_mods == Qt.KeyboardModifier.ShiftModifier:
				self.active_pane.select_all_items()
				return True
			elif event.key() == Qt.Key.Key_Home and \
				_mods == Qt.KeyboardModifier.ShiftModifier:
				self.active_pane.select_all_items()
				return True
			elif event.key() == Qt.Key.Key_M:
				if self.active_pane.selected_paths():
					self.move_selected()
				else:
					self.create_folder()
				return True
			elif event.key() == Qt.Key.Key_R and no_mod:
				self.rename_item()
				return True
			elif event.key() == Qt.Key.Key_H:
				self.show_history()
				return True
			elif event.key() == Qt.Key.Key_B:
				if _mods == Qt.KeyboardModifier.ControlModifier:
					self._toggle_bookmark()
					return True
				if no_mod:
					self._show_bookmark_list(local=True)
					return True
				if _mods == Qt.KeyboardModifier.ShiftModifier:
					self._show_bookmark_list(local=False)
					return True
			elif event.key() == Qt.Key.Key_Escape and \
				event.modifiers() == Qt.KeyboardModifier.NoModifier:
				if self.active_pane.has_search_results():
					self.active_pane.clear_search_results()
					return True
				if self.active_pane.has_filter():
					self.active_pane.clear_filter()
					return True
			elif event.key() == Qt.Key.Key_D and \
				event.modifiers() == Qt.KeyboardModifier.NoModifier:
				self.select_drive()
				return True
			elif event.key() == Qt.Key.Key_F and \
				event.modifiers() == Qt.KeyboardModifier.NoModifier:
				self.show_filter()
				return True
			elif event.key() == Qt.Key.Key_F and \
				event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
				self.search_files()
				return True
			elif event.key() == Qt.Key.Key_O and \
				event.modifiers() == Qt.KeyboardModifier.NoModifier:
				self.move_to_inactive_path()
				return True
			elif event.key() == Qt.Key.Key_O and \
				event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
				self.move_inactive_to_active_path()
				return True
			elif event.key() == Qt.Key.Key_P and \
				event.modifiers() == Qt.KeyboardModifier.NoModifier:
				self.create_archive()
				return True
			elif event.key() == Qt.Key.Key_U and \
				event.modifiers() == Qt.KeyboardModifier.NoModifier:
				self.extract_archives()
				return True
			elif event.key() == Qt.Key.Key_K:
				if _mods == Qt.KeyboardModifier.ShiftModifier:
					self.trash_selected()
					return True
				if no_mod:
					self.delete_selected()
					return True
			elif event.key() == Qt.Key.Key_S and \
				event.modifiers() == Qt.KeyboardModifier.NoModifier:
				self.sort_files()
				return True
			elif event.key() == Qt.Key.Key_Backspace and no_mod:
				self.active_pane.go_to_parent()
				return True
			elif event.key() == Qt.Key.Key_Q and no_mod:
				confirm = QMessageBox.question(
					self,
					"終了確認",
					"アプリケーションを終了しますか？",
					QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
					QMessageBox.StandardButton.No,
				)
				if confirm == QMessageBox.StandardButton.Yes:
					self.close()
				return True
			elif event.key() == Qt.Key.Key_J:
				if no_mod:
					return True  # ジャンプリスト（未実装）
				if _mods == Qt.KeyboardModifier.ShiftModifier:
					self._jump_to_input_path()
					return True
			elif event.text() == "~":
				self.active_pane.navigate_to(Path.home())
				return True
			elif event.key() == Qt.Key.Key_G and \
				event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
				self._pending_g = False
				self.active_pane.go_to_last_item()
				return True
			elif event.key() == Qt.Key.Key_G and \
				event.modifiers() == Qt.KeyboardModifier.NoModifier:
				if self._pending_g:
					self._pending_g = False
					self.active_pane.go_to_first_item()
				else:
					self._pending_g = True
				return True
			if self._pending_g:
				self._pending_g = False
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
		names = "\n".join(src.name for src in sources)
		confirm = QMessageBox.question(
			self,
			f"ドロップ {op} 確認",
			f"次の項目を {op} しますか？\n\n{dest}\n\n{names}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return

		if not self.confirm_overwrite(sources, dest):
			return

		if is_move:
			self._start_file_operation(
				lambda s=sources, d=dest: move_paths(s, d, overwrite=True),
				"移動しています...",
				f"{len(sources)}件を移動しました。",
			)
		else:
			self._start_file_operation(
				lambda s=sources, d=dest: copy_paths(s, d, overwrite=True),
				"コピーしています...",
				f"{len(sources)}件をコピーしました。",
			)

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

