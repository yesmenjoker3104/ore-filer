from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit, QMessageBox

from ore_filer.gui.dialogs.common import confirm_list
from ore_filer.gui.dialogs.file_list_dialogs import ArchiveDialog
from ore_filer.gui.pane import PaneWidget
from ore_filer.gui.threads import ArchiveCopyThread, ArchiveThread, ExtractArchiveThread
from ore_filer.services.file_operations import ArchivePasswordError


class MainWindowArchiveMixin:
	"""アーカイブの作成、展開、エントリのコピー。"""


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
			except Exception as error:  # noqa: BLE001
				QMessageBox.critical(
					self,
					"アーカイブを開けません",
					str(error),
				)
				return


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
		if existing and not confirm_list(
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
