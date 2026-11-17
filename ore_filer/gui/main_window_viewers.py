from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
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
	HtmlViewerDialog,
	ImageViewerDialog,
	MarkdownViewerDialog,
	PdfViewerDialog,
	SvgViewerDialog,
)
from ore_filer.services.file_operations import read_text_preview
from ore_filer.services.office_text import OFFICE_EXTENSIONS, extract_office_text


class MainWindowViewersMixin:
	"""ファイルを内蔵ビューアで開く処理。"""


	def _open_image_viewer(self, path: Path) -> None:
		try:
			# 画像ビューア表示中にメインウィンドウのカーソルを追従させるため、開いたペインを記録する
			self._image_viewer_pane = self.active_pane
			viewer = getattr(self, "_image_viewer", None)
			if viewer is None:
				viewer = ImageViewerDialog(path, self, on_changed=self._on_image_viewer_changed)
				self._image_viewer = viewer
			else:
				viewer.show_path(path)
			viewer.show()
			viewer.raise_()
			viewer.activateWindow()
		except Exception as error:  # noqa: BLE001
			QMessageBox.critical(self, "画像ビューア エラー", str(error))


	def _on_image_viewer_changed(self, path: Path, retries: int = 5) -> None:
		"""ビューアで表示中の画像をメインウィンドウのカーソルに反映する。
		追加直後のファイルはペインの一覧に未反映のことがあるため、見つからなければ少し待って再試行する。"""
		pane = getattr(self, "_image_viewer_pane", None)
		if pane is None:
			return
		try:
			if pane.current_path.resolve() != path.parent.resolve():
				return
			if pane.has_file_name(path.name) or retries <= 0:
				pane.focus_name(path.name)
			else:
				QTimer.singleShot(200, lambda: self._on_image_viewer_changed(path, retries - 1))
		except RuntimeError:
			# タブ削除などで対象ペインの C++ オブジェクトが破棄済みの場合
			pass


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
		lines = self.active_pane.grep_lines(path)
		TextViewerDialog(path, text, encoding, highlight_lines=lines or None, parent=self).exec()


	def _open_office_viewer(self, path: Path) -> None:
		"""Office 文書（xlsx/docx/pptx 等）をテキスト抽出して TextViewerDialog で表示する。
		GREP ヒット行ハイライト付き。"""
		text = extract_office_text(path)
		if not text:
			QMessageBox.warning(self, "Office Viewer", "テキストを抽出できませんでした。")
			return
		lines = self.active_pane.grep_lines(path)
		TextViewerDialog(
			path, text, "utf-8 (extracted)",
			highlight_lines=lines or None, parent=self,
		).exec()

	def _open_hex_viewer(self, path: Path) -> None:
		HexViewerDialog(path, self).exec()

	def _open_unknown_file(self, path: Path) -> None:
		try:
			text, encoding = read_text_preview(path)
		except (ValueError, OSError):
			HexViewerDialog(path, self).exec()
			return
		lines = self.active_pane.grep_lines(path)
		TextViewerDialog(path, text, encoding, highlight_lines=lines or None, parent=self).exec()


	# ── テキストビューア ──────────────────────────────────────

	def view_text(self) -> None:
		path = self.focused_path()
		if path is None or path.is_dir():
			return
		# アーカイブ内はテキストビューアで直接開く
		if self.active_pane.is_archive_view():
			try:
				text, encoding = read_text_preview(path)
			except (ValueError, OSError) as e:
				QMessageBox.warning(self, "テキストビューア", str(e))
				return
			lines = self.active_pane.grep_lines(path)
			TextViewerDialog(path, text, encoding, highlight_lines=lines or None, parent=self).exec()
			return
		# 通常ファイルは Enter と同じ自動ビューア選択
		self.enter_current_item()
