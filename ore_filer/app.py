import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from ore_filer.gui.main_window import MainWindow
from ore_filer.settings import load_session


def _session_start_path(history: list[str]) -> Path:
    for value in history:
        path = Path(value).expanduser()
        if path.is_dir():
            return path.resolve()
    return Path.home()


def main() -> int:
    app = QApplication(sys.argv)
    session = load_session()
    history = session["history"]
    start_path = _session_start_path(history)
    window = MainWindow(
        start_path,
        start_path,
        history=history,
    )
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())