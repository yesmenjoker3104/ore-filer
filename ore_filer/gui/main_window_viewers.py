from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ore_filer.gui.dialogs.text_viewers import (
	CsvViewerDialog,
	HexViewerDialog,
	JsonViewerDialog,
	LogViewerDialog,
	TextViewerDialog,
	XmlViewerDialog,
)
from ore_filer.gui.dialogs.viewers import (
	IMAGE_EXTENSIONS,
	HtmlViewerDialog,
	ImageViewerDialog,
	MarkdownViewerDialog,
	PdfViewerDialog,
	SvgViewerDialog,
)
from ore_filer.services.file_operations import read_text_preview


class MainWindowViewersMixin:
	"""ファイルを内蔵ビューアで開く処理。"""


	def _open_image_viewer(self, path: Path) -> None:
		try:
			parent = path.parent
			images = sorted(
				[p for p in parent.iterdir() if p.is_file() and p.suffix.casefold() in IMAGE_EXTENSIONS],
				key=lambda p: p.name.casefold(),
			)
			if not images:
				return
			try:
				index = next(i for i, p in enumerate(images) if p.name.casefold() == path.name.casefold())
			except StopIteration:
				index = 0
			dialog = ImageViewerDialog(images, index, self)
			dialog.exec()
		except Exception as error:  # noqa: BLE001
			QMessageBox.critical(self, "画像ビューア エラー", str(error))


	def _open_markdown_viewer(self, path: Path) -> None:
		try:
			text, encoding = read_text_preview(path)
		except ValueError as error:
			QMessageBox.warning(self, "Markdown Viewer", str(error))
			return
		except OSError as error:
			QMessageBox.critical(self, "Markdown Viewer", f"読み込みに失敗しました：\n{error}")
			return
		dialog = MarkdownViewerDialog(path, text, encoding, self)
		dialog.exec()


	def _open_html_viewer(self, path: Path) -> None:
		HtmlViewerDialog(path, self).exec()


	def _open_pdf_viewer(self, path: Path) -> None:
		try:
			PdfViewerDialog(path, self).exec()
		except Exception as error:  # noqa: BLE001
			QMessageBox.critical(self, "PDF Viewer", f"PDF を開けませんでした：\n{error}")


	def _open_csv_viewer(self, path: Path) -> None:
		CsvViewerDialog(path, self).exec()


	def _open_json_viewer(self, path: Path) -> None:
		JsonViewerDialog(path, self).exec()


	def _open_xml_viewer(self, path: Path) -> None:
		XmlViewerDialog(path, self).exec()


	def _open_log_viewer(self, path: Path) -> None:
		LogViewerDialog(path, self).exec()


	def _open_svg_viewer(self, path: Path) -> None:
		try:
			SvgViewerDialog(path, self).exec()
		except Exception as error:  # noqa: BLE001
			QMessageBox.critical(self, "SVG Viewer", f"SVG を開けませんでした：\n{error}")


	def _open_plaintext_viewer(self, path: Path) -> None:
		try:
			text, encoding = read_text_preview(path)
		except ValueError as error:
			QMessageBox.warning(self, "テキスト Viewer", str(error))
			return
		except OSError as error:
			QMessageBox.critical(self, "テキスト Viewer", f"読み込みに失敗しました：\n{error}")
			return
		TextViewerDialog(path, text, encoding, self).exec()


	def _open_hex_viewer(self, path: Path) -> None:
		HexViewerDialog(path, self).exec()


	# ── テキストビューア ──────────────────────────────────────

	def view_text(self) -> None:
		path = self.focused_path()
		if path is None or path.is_dir() or self.active_pane.is_archive_view():
			return
		try:
			text, encoding = read_text_preview(path)
		except ValueError as e:
			QMessageBox.warning(self, "テキストビューア", str(e))
			return
		except OSError as e:
			QMessageBox.warning(self, "テキストビューア", f"読み込みに失敗しました:\n{e}")
			return
		lines = self.active_pane.grep_lines(path)
		TextViewerDialog(path, text, encoding, highlight_lines=lines or None, parent=self).exec()
