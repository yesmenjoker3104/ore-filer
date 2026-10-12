from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QInputDialog, QMessageBox

from ore_filer.gui.dialogs.common import confirm_list
from ore_filer.gui.threads import FileOperationThread
from ore_filer.services.file_operations import (
	copy_paths,
	copy_paths_with_structure,
	create_directory,
	delete_paths,
	move_paths,
	rename_path,
	trash_paths,
)


class MainWindowFileOpsMixin:
	"""コピー、移動、削除、作成などのファイル操作とリモート転送。"""


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
		if self.active_pane.is_remote_view() or self.inactive_pane.is_remote_view():
			self._transfer_remote()
			return
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


	def _transfer_remote(self) -> None:
		active = self.active_pane
		inactive = self.inactive_pane
		if active.is_remote_view() and inactive.is_remote_view():
			QMessageBox.warning(self, "リモート転送", "リモート同士のコピーはできません。")
			return
		if active.is_archive_view() or inactive.is_archive_view():
			QMessageBox.warning(self, "リモート転送", "アーカイブとの間ではコピーできません。")
			return

		if active.is_remote_view():
			entries = active.selected_remote_entries()
			if not entries:
				return
			session = active.remote_session()
			local_dir = inactive.current_path
			names = [entry.name for entry in entries]
			if session is None or not confirm_list(
				self,
				"ダウンロード確認",
				f"次の項目をダウンロードしますか？\n\n{local_dir}",
				names,
			):
				return
			existing = [name for name in names if (local_dir / name).exists()]
			if existing and not confirm_list(
				self,
				"上書き確認",
				"次の項目は既に存在します。上書きしますか？",
				existing,
			):
				return
			self._start_file_operation(
				lambda cb, s=session, e=entries, d=local_dir: s.download_entries(e, d, cb),
				"ダウンロードしています...",
				f"{len(entries)}件をダウンロードしました。",
				total=len(entries),
			)
			return

		sources = active.selected_paths()
		if not sources:
			return
		session = inactive.remote_session()
		remote_dir = inactive.remote_directory()
		names = [path.name for path in sources]
		if session is None or not confirm_list(
			self,
			"アップロード確認",
			f"次の項目をアップロードしますか？\n\n{remote_dir}",
			names,
		):
			return
		remote_names = inactive.remote_names()
		existing = [name for name in names if name in remote_names]
		if existing and not confirm_list(
			self,
			"上書き確認",
			"次の項目は既に存在します。上書きしますか？",
			existing,
		):
			return
		self._start_file_operation(
			lambda cb, s=session, p=sources, d=remote_dir: s.upload_paths(p, d, cb),
			"アップロードしています...",
			f"{len(sources)}件をアップロードしました。",
			total=len(sources),
		)

		
	def create_folder(self) -> None:
		text, ok = QInputDialog.getMultiLineText(
			self,
			"新規フォルダの作成",
			"フォルダ名（1行1フォルダ。Ctrl+Enter で確定）:",
		)
		if not ok or not text.strip():
			return
		names = [line.strip() for line in text.splitlines() if line.strip()]
		errors: list[str] = []
		for folder_name in names:
			new_folder_path = self.active_pane.current_path / folder_name
			if new_folder_path.exists():
				errors.append(f"{folder_name}: 同名のフォルダまたはファイルが存在します")
				continue
			try:
				create_directory(self.active_pane.current_path, folder_name)
			except Exception as e:  # noqa: BLE001
				errors.append(f"{folder_name}: {e}")
		self.active_pane.reload()
		if errors:
			QMessageBox.warning(self, "警告", "\n".join(errors))


	def rename_item(self) -> None:
		if len(self.active_pane.selected_paths()) > 1:
			self.bulk_rename()
			return
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
			except Exception as e:  # noqa: BLE001
				QMessageBox.critical(
					self,
					"エラー",
					f"名前の変更に失敗しました:\n{e}"
				)
				return
			self.active_pane.reload()


	@staticmethod
	def _calc_total_size(paths: list) -> int:
		total = 0
		for p in paths:
			try:
				if p.is_dir():
					total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
				else:
					total += p.stat().st_size
			except OSError:
				pass
		return total

	def delete_selected(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return

		if not confirm_list(
			self,
			"削除確認",
			"次の項目を完全削除しますか？",
			[path.name for path in paths],
			total_size=self._calc_total_size(paths),
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
			total_size=self._calc_total_size(paths),
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
					self.statusBar().showMessage(f"{msg.removesuffix('...')} {done}/{t}件...")
			)
		thread.completed.connect(lambda msg=done_message: self._file_op_completed(msg))
		thread.failed.connect(self._file_op_failed)
		thread.finished.connect(self._file_op_thread_finished)
		self._file_op_thread = thread
		thread.start()


	def _file_op_completed(self, message: str) -> None:
		self.left_pane.reload()
		self.right_pane.reload()
		self.statusBar().showMessage(message)


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
