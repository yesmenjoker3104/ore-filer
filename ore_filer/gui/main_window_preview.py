"""プレビューモード ミックスイン。"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QStackedWidget

from ore_filer.gui.preview_panel import PreviewPanel


class MainWindowPreviewMixin:
    """非アクティブ側にカーソル項目のプレビューを表示するモード。

    MainWindow.__init__ から _setup_preview() を呼ぶこと（_setup_tabs() の後）。
    _switch_pane / _update_active_pane の後に _update_preview_visibility() を呼ぶこと。
    """

    def _setup_preview(self) -> None:
        """スタックウィジェット・プレビューパネル・タイマーを作り splitter に追加する。
        _setup_tabs() の後に呼ぶこと。"""
        # 左右それぞれ QStackedWidget[tabs, preview]
        self._left_stack = QStackedWidget(self)
        self._left_stack.addWidget(self._left_tabs)   # index 0
        self._left_preview = PreviewPanel(self)
        self._left_stack.addWidget(self._left_preview)  # index 1

        self._right_stack = QStackedWidget(self)
        self._right_stack.addWidget(self._right_tabs)  # index 0
        self._right_preview = PreviewPanel(self)
        self._right_stack.addWidget(self._right_preview)  # index 1

        self.splitter.addWidget(self._left_stack)
        self.splitter.addWidget(self._right_stack)

        self._preview_enabled = False

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._update_preview_content)
        # current_item_changed の接続は _setup_pane (MainWindowTabsMixin) が担う

    # ── プレビューモード制御 ──────────────────────────────────

    def toggle_preview(self) -> None:
        self._preview_enabled = not self._preview_enabled
        self._update_preview_visibility()
        label = "ON" if self._preview_enabled else "OFF"
        self.statusBar().showMessage(f"プレビューモード: {label}", 3000)

    def _update_preview_visibility(self) -> None:
        """アクティブ側はペイン表示、非アクティブ側はプレビューに切り替える。"""
        if not getattr(self, "_preview_enabled", False):
            self._left_stack.setCurrentIndex(0)
            self._right_stack.setCurrentIndex(0)
            return

        if self.active_pane_index == 0:
            self._left_stack.setCurrentIndex(0)
            self._right_stack.setCurrentIndex(1)
        else:
            self._left_stack.setCurrentIndex(1)
            self._right_stack.setCurrentIndex(0)

        self._update_preview_content()

    # ── カーソル変化ハンドラ ─────────────────────────────────

    def _on_pane_cursor_changed(self, pane) -> None:
        if getattr(self, "_preview_enabled", False) and pane is self.active_pane:
            self._preview_timer.start()  # 既に動作中なら restart

    def _update_preview_content(self) -> None:
        if not getattr(self, "_preview_enabled", False):
            return
        path = self.focused_path()
        self._inactive_preview.set_path(path)

    @property
    def _inactive_preview(self) -> PreviewPanel:
        return self._right_preview if self.active_pane_index == 0 else self._left_preview
