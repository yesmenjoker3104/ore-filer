"""ペインごとのタブ管理ミックスイン。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QTabWidget

from ore_filer.gui.pane import PaneWidget


class MainWindowTabsMixin:
	"""左右それぞれのペインをタブで管理する。

	MainWindow.__init__ から _setup_tabs() を呼ぶこと。
	left_pane / right_pane / panes は MainWindow 側でプロパティとして定義する。
	"""

	def _setup_tabs(
		self,
		left_path: str | Path,
		right_path: str | Path,
		history: list,
	) -> None:
		"""QTabWidget を作成し最初のペインを追加する。splitter への追加は _setup_preview が行う。"""
		self._shared_history = history

		self._left_tabs = QTabWidget()
		self._left_tabs.setTabBarAutoHide(True)
		self._left_tabs.setDocumentMode(True)

		self._right_tabs = QTabWidget()
		self._right_tabs.setTabBarAutoHide(True)
		self._right_tabs.setDocumentMode(True)

		left_pane = PaneWidget(str(left_path), history=history)
		title_l = Path(str(left_path)).name or str(left_path)
		self._left_tabs.addTab(left_pane, title_l)
		self._setup_pane(left_pane)

		right_pane = PaneWidget(str(right_path), history=history)
		title_r = Path(str(right_path)).name or str(right_path)
		self._right_tabs.addTab(right_pane, title_r)
		self._setup_pane(right_pane)

		self._left_tabs.currentChanged.connect(lambda _: self._on_tab_changed())
		self._right_tabs.currentChanged.connect(lambda _: self._on_tab_changed())

	def _on_tab_changed(self) -> None:
		self._update_active_pane()
		self._update_preview_visibility()

	def _setup_pane(self, pane: PaneWidget) -> None:
		"""新しいペインに共通セットアップを適用する。"""
		from ore_filer.settings import load_config

		pane.file_view.installEventFilter(self)
		pane.file_view.viewport().installEventFilter(self)

		if hasattr(self, "_bookmark_set"):
			pane.bookmarks = self._bookmark_set

		pane.selection_changed.connect(self._update_status_bar)
		pane.path_changed.connect(self._update_status_bar)
		pane.files_dropped.connect(self._on_files_dropped)
		pane.remote_error.connect(self._show_remote_error)
		pane.path_changed.connect(lambda _path, p=pane: self._refresh_git_status(p))
		pane.path_changed.connect(lambda path, p=pane: self._update_pane_tab_title(p, path))

		cfg = load_config()
		pane.set_show_hidden(cfg.get("show_hidden", True))

		# 新規タブ作成時はフォントサイズを既存ペインに合わせる
		_peers = [
			p for p in (
				self._left_tabs.currentWidget() if hasattr(self, "_left_tabs") else None,
				self._right_tabs.currentWidget() if hasattr(self, "_right_tabs") else None,
			)
			if p is not None and p is not pane
		]
		if _peers:
			sz = _peers[0]._font_size
			if sz:
				pane.adjust_font_size(sz - pane._font_size)

		# プレビューのカーソル変化ハンドラ（_preview_enabled が未初期化でも安全）
		pane.current_item_changed.connect(lambda p=pane: self._on_pane_cursor_changed(p))

	def _update_pane_tab_title(self, pane: PaneWidget, path) -> None:
		"""path_changed に接続し、タブタイトルをフォルダ名に更新する。"""
		title = Path(str(path)).name or str(path)
		for tabs in (self._left_tabs, self._right_tabs):
			for i in range(tabs.count()):
				if tabs.widget(i) is pane:
					tabs.setTabText(i, title)
					return

	# ── タブ操作 ─────────────────────────────────────────────

	def tab_new(self) -> None:
		"""アクティブ側に現在フォルダで新タブを追加する。"""
		tabs = self._left_tabs if self.active_pane_index == 0 else self._right_tabs
		cur_path = str(self.active_pane.current_path)
		pane = PaneWidget(cur_path, history=self._shared_history)
		title = Path(cur_path).name or cur_path
		tabs.addTab(pane, title)
		tabs.setCurrentWidget(pane)
		self._setup_pane(pane)
		pane.file_view.setFocus()

	def tab_close(self) -> None:
		"""アクティブ側の現在タブを閉じる（最後の1枚は閉じない）。"""
		tabs = self._left_tabs if self.active_pane_index == 0 else self._right_tabs
		if tabs.count() <= 1:
			return
		idx = tabs.currentIndex()
		pane = tabs.widget(idx)
		tabs.removeTab(idx)
		if pane is not None:
			pane.deleteLater()
		self._update_active_pane()
		self._update_preview_visibility()

	def tab_next(self) -> None:
		"""アクティブ側の次のタブへ移動する。"""
		tabs = self._left_tabs if self.active_pane_index == 0 else self._right_tabs
		if tabs.count() > 1:
			tabs.setCurrentIndex((tabs.currentIndex() + 1) % tabs.count())

	def tab_prev(self) -> None:
		"""アクティブ側の前のタブへ移動する。"""
		tabs = self._left_tabs if self.active_pane_index == 0 else self._right_tabs
		if tabs.count() > 1:
			tabs.setCurrentIndex((tabs.currentIndex() - 1 + tabs.count()) % tabs.count())

	def restore_tabs(
		self,
		left_tabs: list[str],
		left_idx: int,
		right_tabs: list[str],
		right_idx: int,
	) -> None:
		"""セッション復元でタブを追加する（app.py から呼ばれる）。"""
		for path_str in left_tabs[1:]:
			path = Path(path_str)
			if path.is_dir():
				pane = PaneWidget(str(path), history=self._shared_history)
				self._left_tabs.addTab(pane, path.name or str(path))
				self._setup_pane(pane)
		self._left_tabs.setCurrentIndex(
			min(left_idx, self._left_tabs.count() - 1)
		)

		for path_str in right_tabs[1:]:
			path = Path(path_str)
			if path.is_dir():
				pane = PaneWidget(str(path), history=self._shared_history)
				self._right_tabs.addTab(pane, path.name or str(path))
				self._setup_pane(pane)
		self._right_tabs.setCurrentIndex(
			min(right_idx, self._right_tabs.count() - 1)
		)
