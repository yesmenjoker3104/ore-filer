from pathlib import Path

from PySide6.QtCore import QDir, QEvent, QThread, Qt, Signal
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
	FilterDialog,
	HistoryDialog,
	SearchDialog,
)
from ore_filer.gui.pane import PaneWidget
from ore_filer.settings import save_session
from ore_filer.services.file_operations import (
	ArchiveEntry,
	ArchivePasswordError,
	copy_paths,
	copy_archive_entries,
	create_directory,
	delete_paths,
	is_archive_path,
	rename_path,
	move_paths,
	open_with_association,
	create_archive,
	extract_archive,
)


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

		splitter = QSplitter(Qt.Orientation.Horizontal)
		splitter.addWidget(self.left_pane)
		splitter.addWidget(self.right_pane)
		splitter.setSizes([600, 600])

		self.setCentralWidget(splitter)

		for pane in self.panes:
			pane.file_view.installEventFilter(self)
			pane.file_view.viewport().installEventFilter(self)

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
		save_session(self.left_pane.history_paths())
		super().closeEvent(event)

	def show_history(self) -> None:
		history = self.active_pane.history_paths()
		if not history:
			return

		dialog = HistoryDialog(history, self)
		if dialog.exec() == QDialog.DialogCode.Accepted:
			selected = dialog.selected_path()
			if selected is not None:
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
		return False

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

		if confirm == QMessageBox.StandardButton.Yes:
			if not self.confirm_overwrite(source, destination):
				return
			copy_paths(source, destination, overwrite=True)
			self.active_pane.file_view.selectionModel().clearSelection()
			self.inactive_pane.reload()
		else:
			return

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
			f"次の項目を削除しますか？\n\n{chr(10).join(path.name for path in paths)}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return

		try:
			delete_paths(paths)
		except OSError as error:
			QMessageBox.critical(self, "削除エラー", str(error))
			return

		self.active_pane.file_view.selectionModel().clearSelection()
		self.active_pane.reload()

	def move_selected(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return

		destination = self.inactive_pane.current_path
		if not self.confirm_overwrite(paths, destination):
			return

		try:
			move_paths(paths, destination, overwrite=True)
		except Exception as error:
			QMessageBox.critical(
				self,
				"エラー",
				f"移動に失敗しました:\n{error}"
			)
			return

		self.active_pane.file_view.selectionModel().clearSelection()
		self.active_pane.reload()
		self.inactive_pane.reload()

	def eventFilter(self, watched, event) -> bool:
		if event.type() == QEvent.Type.KeyPress:
			if (event.key() == Qt.Key.Key_Tab) or \
				(event.key() == Qt.Key.Key_Right and self.active_pane_index == 0) or \
				(event.key() == Qt.Key.Key_Left and self.active_pane_index == 1):
				self._switch_pane()
				return True
			
			elif event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
				if event.modifiers() == Qt.KeyboardModifier.ShiftModifier:
					self.execute_associated()
					return True
				if event.modifiers() == Qt.KeyboardModifier.NoModifier:
					return self.enter_current_item()
				
			elif (event.key() == Qt.Key.Key_Left and self.active_pane_index == 0) or \
				(event.key() == Qt.Key.Key_Right and self.active_pane_index == 1):
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
				self.copy_selected()
				return True
			elif event.key() == Qt.Key.Key_Space:
				self.active_pane.select_current_and_move_down()
				return True
			elif event.key() == Qt.Key.Key_M:
				if self.active_pane.selected_paths():
					self.move_selected()
				else:
					self.create_folder()
				return True
			elif event.key() == Qt.Key.Key_R:
				self.rename_item()
				return True
			elif event.key() == Qt.Key.Key_H:
				self.show_history()
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
				self.delete_selected()
				return True
		return super().eventFilter(watched, event)

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
