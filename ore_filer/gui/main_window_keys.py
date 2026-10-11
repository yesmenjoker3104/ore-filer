from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QMessageBox

from ore_filer.gui.dialogs.file_list_dialogs import KeymapHelpDialog
from ore_filer.gui.keymap import user_keymap_file


class MainWindowKeysMixin:
	"""キー入力の振り分けとキー設定の監視。"""


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
			elif action == "reload" or action == "refresh":
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
			elif action == "open_with_app":
				self.open_with_registered_app()
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
			elif action == "remote_connect":
				if self.active_pane.is_remote_view():
					self.disconnect_remote()
				else:
					self.connect_remote()
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
