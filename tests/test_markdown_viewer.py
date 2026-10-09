import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTextBrowser

from ore_filer.gui.dialogs import MARKDOWN_EXTENSIONS, MarkdownViewerDialog
from ore_filer.gui.main_window import MainWindow


class _EnterHarness:
    def __init__(self, path: Path):
        self.panes = [SimpleNamespace(is_archive_view=lambda: False)]
        self.active_pane_index = 0
        self.path = path
        self.opened_paths: list[Path] = []

    @property
    def active_pane(self):
        return self.panes[self.active_pane_index]

    def focused_path(self) -> Path:
        return self.path

    def _open_markdown_viewer(self, path: Path) -> None:
        self.opened_paths.append(path)


class MarkdownViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_markdown_extensions_are_case_insensitive(self) -> None:
        for suffix in (".md", ".markdown", ".mdown", ".mkd"):
            self.assertIn(suffix, MARKDOWN_EXTENSIONS)
            self.assertIn(Path(f"README{suffix.upper()}").suffix.casefold(), MARKDOWN_EXTENSIONS)

    def test_markdown_dialog_renders_markdown(self) -> None:
        dialog = MarkdownViewerDialog(
            Path("README.md"),
            "# Heading\n\n**bold text**",
            "utf-8",
        )
        try:
            viewer = dialog.findChild(QTextBrowser)
            self.assertIsNotNone(viewer)
            assert viewer is not None
            self.assertIn("Heading", viewer.toPlainText())
            self.assertIn("bold text", viewer.toPlainText())
            self.assertIn("<h1", viewer.toHtml().lower())
        finally:
            dialog.close()
            dialog.deleteLater()
            self.app.processEvents()

    def test_enter_opens_markdown_viewer_for_markdown_path(self) -> None:
        path = Path("notes.MD")
        harness = _EnterHarness(path)

        handled = MainWindow.enter_current_item(harness)

        self.assertTrue(handled)
        self.assertEqual(harness.opened_paths, [path])


if __name__ == "__main__":
    unittest.main()
