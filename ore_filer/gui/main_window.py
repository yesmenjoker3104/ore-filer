from __future__ import annotations

import base64
from pathlib import Path

from PySide6.QtCore import QDir, Qt, QThread, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QInputDialog, QMainWindow, QMessageBox, QSplitter

from ore_filer.gui.app_launcher_dialog import AppLauncherDialog
from ore_filer.gui.context_menu import show_shell_context_menu
from ore_filer.gui.dialogs.common import confirm_list
from ore_filer.gui.dialogs.diff_dialogs import SideBySideDiffDialog
from ore_filer.gui.dialogs.file_list_dialogs import (
	BookmarkDialog,
	FileInfoDialog,
	FilterDialog,
	HistoryDialog,
	SearchDialog,
	SortDialog,
)
from ore_filer.gui.dialogs.grep_dialog import GrepDialog
from ore_filer.gui.dialogs.rename_compare import BulkRenameDialog, CompareSelectDialog
from ore_filer.gui.dialogs.viewers import (
	CSV_EXTENSIONS,
	HEX_EXTENSIONS,
	HTML_EXTENSIONS,
	IMAGE_EXTENSIONS,
	JSON_EXTENSIONS,
	LOG_EXTENSIONS,
	MARKDOWN_EXTENSIONS,
	PDF_EXTENSIONS,
	PLAINTEXT_EXTENSIONS,
	SVG_EXTENSIONS,
	XML_EXTENSIONS,
)
from ore_filer.services.office_text import OFFICE_EXTENSIONS
from ore_filer.services.undo import UndoEntry, UndoRecord, UndoStack
from ore_filer.gui.keymap import KeyMap
from ore_filer.gui.main_window_archive import MainWindowArchiveMixin
from ore_filer.gui.main_window_file_ops import MainWindowFileOpsMixin
from ore_filer.gui.main_window_git import MainWindowGitMixin
from ore_filer.gui.main_window_keys import MainWindowKeysMixin
from ore_filer.gui.main_window_preview import MainWindowPreviewMixin
from ore_filer.gui.main_window_remote import MainWindowRemoteMixin
from ore_filer.gui.main_window_tabs import MainWindowTabsMixin
from ore_filer.gui.main_window_update import MainWindowUpdateMixin
from ore_filer.gui.main_window_viewers import MainWindowViewersMixin
from ore_filer.gui.pane import PaneWidget
from ore_filer.gui.threads import (
	DirSizeThread,
	FileOperationThread,
	GitStatusThread,
	RemoteConnectThread,
)
from ore_filer.services.app_launcher import find_app_launcher
from ore_filer.services.file_operations import (
	copy_paths,
	create_file,
	is_archive_path,
	move_paths,
	open_in_editor,
	open_terminal,
	open_with_association,
	read_text_pair,
	rename_paths,
	reveal_in_explorer,
)
from ore_filer.settings import (
	load_bookmarks,
	load_config,
	save_bookmarks,
	save_config,
	save_session,
)
from ore_filer.updater import UpdateCheckThread, UpdateDownloadThread


def _fmt_size_status(size: int) -> str:
	if size < 1024:
		return f"{size} B"
	if size < 1024 * 1024:
		return f"{size / 1024:.1f} KB"
	if size < 1024 ** 3:
		return f"{size / (1024 ** 2):.1f} MB"
	return f"{size / (1024 ** 3):.1f} GB"


class MainWindow(MainWindowRemoteMixin, MainWindowArchiveMixin, MainWindowFileOpsMixin, MainWindowViewersMixin, MainWindowGitMixin, MainWindowKeysMixin, MainWindowPreviewMixin, MainWindowTabsMixin, MainWindowUpdateMixin, QMainWindow):
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

		self.active_pane_index = 0
		self._archive_thread: QThread | None = None
		self._file_op_thread: FileOperationThread | None = None
		self._undo_stack = UndoStack()
		self._dir_size_thread: DirSizeThread | None = None
		self._remote_connect_thread: RemoteConnectThread | None = None
		self._remote_target_pane: PaneWidget | None = None

		# キーマップ
		self._keymap = KeyMap()
		self._keymap_watcher = None
		self._setup_keymap_watcher()

		# ブックマーク（_setup_pane が参照するため先に初期化）
		self._bookmarks: list[str] = load_bookmarks()
		self._bookmark_set: set[str] = {b.casefold() for b in self._bookmarks}

		# Git スレッド管理（_setup_pane が path_changed に接続するため先に初期化）
		self._git_status_threads: dict[int, GitStatusThread] = {}
		self._git_windows: dict = {}

		# タブ＋ペイン生成（_setup_pane も内部で呼ばれる）
		self.splitter = QSplitter(Qt.Orientation.Horizontal)
		self._setup_tabs(left_path, right_path, shared_history)
		self._setup_preview()  # _left_tabs/_right_tabs を splitter に追加
		self.splitter.setSizes([600, 600])
		self.setCentralWidget(self.splitter)

		self._update_active_pane()

		self._last_update_check: str = ""
		self._update_check_thread: UpdateCheckThread | None = None
		self._update_download_thread: UpdateDownloadThread | None = None
		QTimer.singleShot(3000, self._check_for_updates)
		# 起動時に両ペインの Git 状態を取得
		for pane in self.panes:
			self._refresh_git_status(pane)

	@property
	def left_pane(self) -> PaneWidget:
		return self._left_tabs.currentWidget()

	@property
	def right_pane(self) -> PaneWidget:
		return self._right_tabs.currentWidget()

	@property
	def panes(self) -> list[PaneWidget]:
		return [self._left_tabs.currentWidget(), self._right_tabs.currentWidget()]

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
		left_tab_paths = [
			str(self._left_tabs.widget(i).current_path)
			for i in range(self._left_tabs.count())
		]
		right_tab_paths = [
			str(self._right_tabs.widget(i).current_path)
			for i in range(self._right_tabs.count())
		]
		save_session(
			self.left_pane.history_paths(),
			left_path=str(self.left_pane.current_path),
			right_path=str(self.right_pane.current_path),
			geometry=geometry,
			splitter=splitter,
			last_update_check=self._last_update_check,
			font_size=self.left_pane._font_size,
			left_tabs=left_tab_paths,
			right_tabs=right_tab_paths,
			left_tab_index=self._left_tabs.currentIndex(),
			right_tab_index=self._right_tabs.currentIndex(),
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
			except Exception:  # noqa: BLE001, S110
				pass
		if splitter:
			try:
				self.splitter.restoreState(base64.b64decode(splitter))
			except Exception:  # noqa: BLE001, S110
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

	def enter_current_item(self) -> bool:
		if self.active_pane.is_remote_view():
			entry = self.active_pane.remote_entry_from_index(
				self.active_pane.file_view.currentIndex()
			)
			if entry is None or not entry.is_dir:
				return False
			self.active_pane.enter_remote_directory(entry)
			return True

		if self.active_pane.is_archive_view():
			entry = self.active_pane.focused_entry()
			if entry is None or not entry.is_dir:
				return False
			try:
				self.active_pane.enter_archive_directory(entry)
			except Exception as error:  # noqa: BLE001
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
		elif path.suffix.casefold() in OFFICE_EXTENSIONS:
			self._open_office_viewer(path)
			return True
		elif path.suffix.casefold() in PLAINTEXT_EXTENSIONS:
			self._open_plaintext_viewer(path)
			return True
		elif path.suffix.casefold() in HEX_EXTENSIONS:
			self._open_hex_viewer(path)
			return True
		# 未知拡張子: テキスト読み込みを試み、失敗したらHexビューア
		self._open_unknown_file(path)
		return True

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
		self._update_preview_visibility()

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
			self.statusBar().showMessage(f"選択: {sel}件 / {_fmt_size_status(size)}")
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

	# ── 登録アプリで開く ──────────────────────────────────────

	def open_with_registered_app(self) -> None:
		exe = find_app_launcher()
		if exe is None:
			QMessageBox.warning(
				self,
				"アプリランチャー",
				"app-launcherが見つかりません。\n"
				"インストール先を確認するか、config.json の app_launcher_path を設定してください。",
			)
			return
		AppLauncherDialog(exe, self, target_files=self.active_pane.selected_paths()).exec()

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
		undo_record = UndoRecord(
			kind="rename",
			label=f"{len(pairs)}件を一括リネーム",
			entries=[UndoEntry(current=old.parent / new, original=old) for old, new in pairs],
		)
		self._start_file_operation(
			lambda cb, p=pairs: rename_paths(p),
			"リネームしています...",
			f"{len(pairs)}件をリネームしました。",
			on_done=lambda _rec=undo_record: self._undo_stack.push(_rec),
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
					  if self.left_pane.current_path != p}
		right_items = {p.name.casefold(): p for p in self.right_pane.current_path.iterdir()
					   if self.right_pane.current_path != p}

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
			lines.append(f"{path.name}\n  {_fmt_size_status(total_bytes)}  ({total_files:,} ファイル)")
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
		except Exception as e:  # noqa: BLE001
			QMessageBox.critical(self, "エラー", f"ファイルの作成に失敗しました:\n{e}")
			return
		self._undo_stack.push(UndoRecord(
			kind="create",
			label=f"ファイルを作成: {name}",
			entries=[UndoEntry(current=new_path, original=None)],
		))
		self.active_pane.reload()
		self.active_pane._pending_focus = name
		config = load_config()
		editor = config.get("editor", "")
		if editor:
			try:
				open_in_editor(new_path, editor)
			except OSError:
				pass

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
		def _pane_focused(pane: PaneWidget) -> Path | None:
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
