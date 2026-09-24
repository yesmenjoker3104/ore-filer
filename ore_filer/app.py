import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ore_filer.gui.main_window import MainWindow
from ore_filer.settings import load_session

WINDOWS_APP_USER_MODEL_ID = "OreFiler.FileManager"


def _set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return

    import ctypes

    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        WINDOWS_APP_USER_MODEL_ID
    )


def _session_start_path(history: list[str]) -> Path:
    for value in history:
        path = Path(value).expanduser()
        if path.is_dir():
            return path.resolve()
    return Path.home()


def main() -> int:
    _set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    icon = QIcon(str(Path(__file__).with_name("icon.ico")))
    app.setWindowIcon(icon)
    session = load_session()
    history = session["history"]
    start_path = _session_start_path(history)
    window = MainWindow(
        start_path,
        start_path,
        history=history,
    )
    window.setWindowIcon(icon)
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())