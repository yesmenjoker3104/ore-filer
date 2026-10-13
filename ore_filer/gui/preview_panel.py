"""プレビューパネル — 非アクティブ側ペイン位置にカーソル項目のプレビューを表示する。"""
from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ore_filer.gui.dialogs.viewers import IMAGE_EXTENSIONS
from ore_filer.services.office_text import OFFICE_EXTENSIONS, extract_office_text

_PREVIEW_TEXT_LIMIT = 256 * 1024  # 256 KB

_TEXT_EXTENSIONS = frozenset({
    ".txt", ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
    ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".kt", ".scala", ".sh",
    ".bat", ".ps1", ".sql", ".r", ".m", ".yaml", ".yml", ".toml", ".env",
    ".properties", ".ini", ".cfg", ".conf", ".log", ".csv", ".tsv",
    ".xml", ".xhtml", ".xsd", ".xsl", ".xslt", ".wsdl", ".svg",
    ".json", ".jsonl", ".md", ".markdown", ".mdown", ".mkd", ".html", ".htm",
})

_PAGE_INFO  = 0
_PAGE_TEXT  = 1
_PAGE_IMAGE = 2


def _fmt_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 ** 3:
        return f"{size / (1024 ** 2):.1f} MB"
    return f"{size / (1024 ** 3):.1f} GB"


class PreviewPanel(QWidget):
    """ファイル・フォルダ・画像のプレビューを表示するウィジェット。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current_pixmap: QPixmap | None = None

        self._stack = QStackedWidget(self)

        # ── INFO ページ ───────────────────────────────────────
        self._info_label = QLabel()
        self._info_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._info_label.setWordWrap(True)
        self._info_label.setContentsMargins(8, 8, 8, 8)
        self._stack.addWidget(self._info_label)   # index 0

        # ── TEXT ページ ───────────────────────────────────────
        self._text_view = QPlainTextEdit()
        self._text_view.setReadOnly(True)
        font = self._text_view.font()
        font.setFamily("Consolas")
        font.setPointSize(9)
        self._text_view.setFont(font)
        self._stack.addWidget(self._text_view)    # index 1

        # ── IMAGE ページ ──────────────────────────────────────
        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(1, 1)
        self._image_label.setStyleSheet("background: #1a1a1a;")
        self._stack.addWidget(self._image_label)  # index 2

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

    # ── 公開 API ─────────────────────────────────────────────

    def set_path(self, path: Path | None) -> None:
        """プレビュー対象パスを設定して表示を更新する。"""
        self._current_pixmap = None

        if path is None:
            self._show_info("— プレビューなし —")
            return

        try:
            if path.is_dir():
                self._show_folder_info(path)
            elif path.suffix.casefold() in IMAGE_EXTENSIONS:
                self._show_image(path)
            elif path.suffix.casefold() in OFFICE_EXTENSIONS:
                self._show_office(path)
            elif path.suffix.casefold() in _TEXT_EXTENSIONS:
                self._show_text(path)
            else:
                self._show_file_info(path)
        except Exception as e:  # noqa: BLE001
            self._show_info(f"{path.name}\n\n読み込みエラー: {e}")

    # ── 内部メソッド ─────────────────────────────────────────

    def _show_info(self, text: str) -> None:
        self._info_label.setText(text)
        self._stack.setCurrentIndex(_PAGE_INFO)

    def _show_file_info(self, path: Path) -> None:
        try:
            st = path.stat()
            size = _fmt_size(st.st_size)
            mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime(
                "%Y/%m/%d %H:%M:%S"
            )
            text = f"{path.name}\n\nサイズ: {size}\n更新日時: {mtime}"
        except OSError:
            text = path.name
        self._show_info(text)

    def _show_folder_info(self, path: Path) -> None:
        try:
            items = list(path.iterdir())
            n_files = sum(1 for p in items if p.is_file())
            n_dirs  = sum(1 for p in items if p.is_dir())
            text = f"📁 {path.name}\n\nファイル: {n_files} 件\nフォルダ: {n_dirs} 件"
        except PermissionError:
            text = f"📁 {path.name}\n\n（アクセス拒否）"
        self._show_info(text)

    def _show_text(self, path: Path) -> None:
        from ore_filer.services.file_operations import read_text_preview
        try:
            text, encoding = read_text_preview(path, limit=_PREVIEW_TEXT_LIMIT)
        except (ValueError, OSError) as e:
            self._show_info(f"{path.name}\n\n{e}")
            return
        self._text_view.setPlainText(text)
        self._stack.setCurrentIndex(_PAGE_TEXT)

    def _show_office(self, path: Path) -> None:
        text = extract_office_text(path)
        if not text:
            self._show_file_info(path)
            return
        # 先頭 _PREVIEW_TEXT_LIMIT 文字に限定
        if len(text) > _PREVIEW_TEXT_LIMIT:
            text = text[:_PREVIEW_TEXT_LIMIT] + "\n\n[--- 省略 ---]"
        self._text_view.setPlainText(text)
        self._stack.setCurrentIndex(_PAGE_TEXT)

    def _show_image(self, path: Path) -> None:
        px = QPixmap(str(path))
        if px.isNull():
            self._show_file_info(path)
            return
        self._current_pixmap = px
        self._scale_image()
        self._stack.setCurrentIndex(_PAGE_IMAGE)

    def _scale_image(self) -> None:
        if self._current_pixmap is None:
            return
        size = self._image_label.size()
        if size.width() < 1 or size.height() < 1:
            return
        scaled = self._current_pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._stack.currentIndex() == _PAGE_IMAGE:
            self._scale_image()
