from __future__ import annotations

from bisect import bisect_left
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QLabel, QTextBrowser, QVBoxLayout

from ore_filer.gui.image_loader import HEIF_EXTENSIONS, load_image_pixmap

IMAGE_EXTENSIONS = {
    ".bmp", ".gif", ".jpg", ".jpeg", ".png",
    ".tga", ".tif", ".tiff", ".webp",
} | set(HEIF_EXTENSIONS)


MARKDOWN_EXTENSIONS = {
    ".md", ".markdown", ".mdown", ".mkd",
}


HTML_EXTENSIONS = {
    ".html", ".htm",
}


PDF_EXTENSIONS = {
    ".pdf",
}


CSV_EXTENSIONS = {
    ".csv", ".tsv",
}


JSON_EXTENSIONS = {
    ".json", ".jsonl",
}


XML_EXTENSIONS = {
    ".xml", ".xhtml", ".xsd", ".xsl", ".xslt", ".wsdl",
}


SVG_EXTENSIONS = {
    ".svg",
}


LOG_EXTENSIONS = {
    ".log",
}


PLAINTEXT_EXTENSIONS = {
    ".yaml", ".yml", ".toml", ".env", ".properties", ".ini", ".cfg", ".conf",
}


HEX_EXTENSIONS = {
    ".bin", ".dat", ".exe", ".dll", ".so", ".dylib", ".class", ".pyc",
    ".obj", ".o", ".a", ".lib", ".pdb", ".iso", ".img", ".rom",
}


class ImageViewerDialog(QDialog):
    """同一フォルダ内の画像を前後に送って表示する非モーダルビューア。

    画像一覧は表示のたびにフォルダを再スキャンするため、表示中に追加された画像も対象になる。
    """

    def __init__(
        self,
        path: Path,
        parent=None,
        on_changed: Callable[[Path], None] | None = None,
    ):
        super().__init__(parent)
        self._directory = path.parent
        self._current = path
        self._on_changed = on_changed
        self._pixmap: QPixmap | None = None

        self.setWindowTitle("画像ビューア")
        self.resize(900, 700)

        self._image_label = QLabel(self)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(1, 1)
        self._image_label.setStyleSheet("background: black;")

        self._info_label = QLabel(self)
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        layout.addWidget(self._image_label, 1)
        layout.addWidget(self._info_label)

        self._show(self._scan_images())

    def show_path(self, path: Path) -> None:
        """表示対象を差し替える（既存ビューアの再利用用）。"""
        self._directory = path.parent
        self._current = path
        self._show(self._scan_images())

    def _scan_images(self) -> list[Path]:
        try:
            return sorted(
                (
                    p for p in self._directory.iterdir()
                    if p.is_file() and p.suffix.casefold() in IMAGE_EXTENSIONS
                ),
                key=lambda p: p.name.casefold(),
            )
        except OSError:
            return []

    def _show(self, paths: list[Path]) -> None:
        path = self._current
        self._pixmap = None
        if path.is_file():
            pixmap = load_image_pixmap(path)
            if pixmap.isNull():
                self._image_label.setText("読み込めませんでした")
            else:
                self._pixmap = pixmap
                self._update_display()
        else:
            self._image_label.setText("ファイルが見つかりません")

        position = next(
            (i for i, p in enumerate(paths) if p.name.casefold() == path.name.casefold()),
            None,
        )
        count = f"{position + 1} / {len(paths)}" if position is not None else f"{len(paths)} 件"
        self._info_label.setText(f"{path.name}  ({count})")
        self.setWindowTitle(f"画像ビューア - {path.name}")

        if self._on_changed is not None and path.is_file():
            self._on_changed(path)
    def _update_display(self) -> None:
        if self._pixmap is None:
            return
        scaled = self._pixmap.scaled(
            self._image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_display()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Space):
            self._navigate(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._navigate(-1)
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Q, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.accept()
        else:
            super().keyPressEvent(event)

    def _navigate(self, delta: int) -> None:
        paths = self._scan_images()
        if not paths:
            self._show(paths)
            return
        # 現在ファイルが一覧から消えていた場合は、名前順での挿入位置を基準にする
        key = self._current.name.casefold()
        keys = [p.name.casefold() for p in paths]
        pos = bisect_left(keys, key)
        if pos < len(keys) and keys[pos] == key:
            new_index = (pos + delta) % len(paths)
        else:
            new_index = (pos if delta > 0 else pos - 1) % len(paths)
        self._current = paths[new_index]
        self._show(paths)


# ── マークダウンビューア ──────────────────────────────────────
class MarkdownViewerDialog(QDialog):
    def __init__(
        self,
        path: Path,
        text: str,
        encoding: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"MarkdownViewer - {path.name}  [{encoding}]")
        self.resize(900, 650)

        viewer = QTextBrowser(self)
        viewer.setOpenExternalLinks(True)
        viewer.setMarkdown(text)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)
        layout.addWidget(viewer)

    def keyPressEvent(self, event) -> None:
        if event.key() in (
             Qt.Key.Key_Escape,
             Qt.Key.Key_Q,
             ):
            self.accept()
            return
        super().keyPressEvent(event)


# ── HTML ビューア ────────────────────────────────────────


class HtmlViewerDialog(QDialog):
    """HTML ファイルの簡易ビューア（QTextBrowser ベース）。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"HTML Viewer - {path.name}")
        self.resize(960, 700)

        viewer = QTextBrowser(self)
        viewer.setOpenExternalLinks(True)
        viewer.setSearchPaths([str(path.parent)])
        try:
            html = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            html = f"<pre>読み込みに失敗しました: {e}</pre>"
        viewer.setHtml(html)

        hint = QLabel("Esc / Q: 閉じる", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(viewer, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── PDF ビューア ─────────────────────────────────────────


class PdfViewerDialog(QDialog):
    """PDF ファイルのビューア（Qt PDF ベース）。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"PDF Viewer - {path.name}")
        self.resize(960, 800)

        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPdfWidgets import QPdfView

        self._doc = QPdfDocument(self)
        self._doc.load(str(path))

        view = QPdfView(self)
        view.setDocument(self._doc)
        view.setPageMode(QPdfView.PageMode.MultiPage)
        view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        hint = QLabel("Esc / Q: 閉じる", self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(view, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)


# ── SVG ビューア ─────────────────────────────────────────


class SvgViewerDialog(QDialog):
    """SVG ファイルのベクタービューア。"""

    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"SVG Viewer - {path.name}")
        self.resize(800, 700)

        from PySide6.QtSvgWidgets import QSvgWidget

        svg_widget = QSvgWidget(str(path), self)
        svg_widget.setStyleSheet("background: white;")

        hint = QLabel("Esc / Q: 閉じる", self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        layout.addWidget(svg_widget, 1)
        layout.addWidget(hint)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q):
            self.accept()
        else:
            super().keyPressEvent(event)
