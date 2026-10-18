from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from ore_filer.gui.dialogs.common import ConflictDialog, MultiLineInputDialog, confirm_list
from ore_filer.gui.threads import FileOperationThread
from ore_filer.services.undo import UndoEntry, UndoRecord
from ore_filer.services.file_operations import (
	copy_items,
	copy_paths,
	copy_paths_with_structure,
	create_directory,
	delete_paths,
	move_items,
	move_paths,
	rename_path,
	trash_paths,
	unique_path,
)


class MainWindowFileOpsMixin:
	"""コピー、移動、削除、作成などのファイル操作とリモート転送。"""


	def _resolve_conflicts(
		self,
		pairs: list[tuple[Path, Path]],
	) -> list[tuple[Path, Path]] | None:
		"""衝突ファイルを UI で 1 件ずつ解決し、確定ペアを返す。
		キャンセル時は None を返す（操作全体を中止）。"""
		resolved: list[tuple[Path, Path]] = []
		apply_all_action: str | None = None

		for src, dst in pairs:
			if not dst.exists():
				resolved.append((src, dst))
				continue

			if apply_all_action is not None:
				action = apply_all_action
			else:
				dlg = ConflictDialog(src, dst, self)
				if dlg.exec() != QDialog.DialogCode.Accepted:
					return None  # キャンセル → 操作全体を中止
				action = dlg.chosen_action()
				if dlg.apply_to_all():
					apply_all_action = action

			if action == "overwrite":
				resolved.append((src, dst))
			elif action == "skip":
				pass
			elif action == "rename":
				resolved.append((src, unique_path(dst)))
			elif action == "newer":
				try:
					if src.stat().st_mtime > dst.stat().st_mtime:
						resolved.append((src, dst))
				except OSError:
					resolved.append((src, dst))  # 比較失敗時は上書き

		return resolved


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
			initial_pairs: list[tuple[Path, Path]] = []
			for path in source:
				try:
					relative = path.relative_to(base_path)
				except ValueError:
					relative = Path(path.name)
				initial_pairs.append((path, destination / relative))
		else:
			initial_pairs = [(path, destination / path.name) for path in source]

		resolved = self._resolve_conflicts(initial_pairs)
		if resolved is None:
			return
		if not resolved:
			self.statusBar().showMessage("コピーする項目がありません（すべてスキップ）", 3000)
			return

		undo_record = UndoRecord(
			kind="copy",
			label=f"{len(resolved)}件をコピー",
			entries=[UndoEntry(current=dst, original=None) for _, dst in resolved],
		)
		self._start_file_operation(
			lambda cb, p=resolved: copy_items(p, progress_cb=cb),
			"コピーしています...",
			f"{len(resolved)}件をコピーしました。",
			total=len(resolved),
			on_done=lambda _rec=undo_record: self._undo_stack.push(_rec),
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
		dlg = MultiLineInputDialog(
			"新規フォルダの作成",
			"フォルダ名（1行1フォルダ、Enter で確定、Shift+Enter で改行）:",
			self,
		)
		if dlg.exec() != QDialog.DialogCode.Accepted:
			return
		text = dlg.text()
		if not text.strip():
			return
		names = [line.strip() for line in text.splitlines() if line.strip()]
		errors: list[str] = []
		created: list[Path] = []
		for folder_name in names:
			new_folder_path = self.active_pane.current_path / folder_name
			if new_folder_path.exists():
				errors.append(f"{folder_name}: 同名のフォルダまたはファイルが存在します")
				continue
			try:
				create_directory(self.active_pane.current_path, folder_name)
				created.append(new_folder_path)
			except Exception as e:  # noqa: BLE001
				errors.append(f"{folder_name}: {e}")
		self.active_pane.reload()
		if created:
			suffix = "…" if len(created) > 3 else ""
			self._undo_stack.push(UndoRecord(
				kind="create",
				label=f"フォルダを作成: {', '.join(p.name for p in created[:3])}{suffix}",
				entries=[UndoEntry(current=p, original=None) for p in created],
			))
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
			self._undo_stack.push(UndoRecord(
				kind="rename",
				label=f"{old_path.name} → {new_name}",
				entries=[UndoEntry(current=old_path.parent / new_name, original=old_path)],
			))
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

		trash_results: list[tuple[Path, Path]] = []

		def _do_trash(cb, _paths=paths):
			trash_results.extend(trash_paths(_paths, progress_cb=cb))

		def _after_trash(_results=trash_results, _count=len(paths)):
			entries = [UndoEntry(current=tp, original=orig) for orig, tp in _results]
			self._undo_stack.push(UndoRecord(
				kind="trash",
				label=f"{_count}件をごみ箱に移動",
				entries=entries,
			))

		self._start_file_operation(
			_do_trash,
			"ごみ箱へ移動しています...",
			f"{len(paths)}件をごみ箱へ移動しました。",
			total=len(paths),
			on_done=_after_trash,
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

		initial_pairs = [(path, destination / path.name) for path in paths]
		resolved = self._resolve_conflicts(initial_pairs)
		if resolved is None:
			return
		if not resolved:
			self.statusBar().showMessage("移動する項目がありません（すべてスキップ）", 3000)
			return

		undo_record = UndoRecord(
			kind="move",
			label=f"{len(resolved)}件を移動",
			entries=[UndoEntry(current=dst, original=src) for src, dst in resolved],
		)
		self._start_file_operation(
			lambda cb, p=resolved: move_items(p, progress_cb=cb),
			"移動しています...",
			f"{len(resolved)}件を移動しました。",
			total=len(resolved),
			on_done=lambda _rec=undo_record: self._undo_stack.push(_rec),
		)


	def _start_file_operation(
		self,
		func: Callable,
		busy_message: str,
		done_message: str,
		total: int = 0,
		on_done: Callable | None = None,
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
		def _completed(msg=done_message, _on_done=on_done):
			self._file_op_completed(msg)
			if _on_done:
				_on_done()
		thread.completed.connect(_completed)
		thread.failed.connect(self._file_op_failed)
		thread.finished.connect(self._file_op_thread_finished)
		self._file_op_thread = thread
		thread.start()


	def undo_last(self) -> None:
		from ore_filer.services.undo import undo_operation
		record = self._undo_stack.peek()
		if record is None:
			self.statusBar().showMessage("取り消す操作がありません", 3000)
			return
		confirm = QMessageBox.question(
			self, "取り消しの確認",
			f"「{record.label}」を取り消しますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return
		self._undo_stack.pop()
		skipped: list[str] = []

		def _do_undo(cb, _rec=record):
			skipped.extend(undo_operation(_rec, progress_cb=cb))

		def _after_undo(_skipped=skipped, _label=record.label):
			if _skipped:
				self.statusBar().showMessage(
					f"「{_label}」を取り消しました（{len(_skipped)}件スキップ）", 5000
				)

		self._start_file_operation(
			_do_undo,
			f"「{record.label}」を取り消しています...",
			f"「{record.label}」を取り消しました。",
			total=len(record.entries),
			on_done=_after_undo,
		)


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
