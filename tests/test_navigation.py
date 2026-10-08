import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView

from ore_filer.gui.keymap import DEFAULT_BINDINGS, _parse_spec
from ore_filer.gui.pane import PaneWidget


class NavigationKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_navigation_key_bindings(self) -> None:
        expected = {
            "go_first": "Ctrl+Up",
            "go_last": "Ctrl+Down",
            "page_up": "Shift+Up",
            "page_down": "Shift+Down",
            "git_menu": "G",
        }
        self.assertEqual(
            {name: DEFAULT_BINDINGS[name] for name in expected},
            expected,
        )
        for spec in expected.values():
            _parse_spec(spec)

    def test_shift_arrow_moves_one_view_page(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for number in range(60):
                (root / f"{number:03}.txt").write_text(str(number), encoding="utf-8")

            pane = PaneWidget(root)
            pane.resize(500, 260)
            pane.show()
            for _ in range(20):
                self.app.processEvents()
                if pane.file_view.model().rowCount(pane.file_view.rootIndex()) == 60:
                    break
                QTest.qWait(20)

            model = pane.file_view.model()
            root_index = pane.file_view.rootIndex()
            self.assertEqual(model.rowCount(root_index), 60)
            pane.file_view.setCurrentIndex(model.index(20, 0, root_index))

            expected_down = pane.file_view.moveCursor(
                QAbstractItemView.CursorAction.MovePageDown,
                Qt.KeyboardModifier.NoModifier,
            )
            pane.move_page_down()
            self.assertEqual(pane.file_view.currentIndex().row(), expected_down.row())

            expected_up = pane.file_view.moveCursor(
                QAbstractItemView.CursorAction.MovePageUp,
                Qt.KeyboardModifier.NoModifier,
            )
            pane.move_page_up()
            self.assertEqual(pane.file_view.currentIndex().row(), expected_up.row())
            pane.close()


if __name__ == "__main__":
    unittest.main()
