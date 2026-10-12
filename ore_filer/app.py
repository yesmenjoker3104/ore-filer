import faulthandler
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ore_filer.gui.main_window import MainWindow
from ore_filer.settings import load_session, session_file
from ore_filer.updater import cleanup_old_exe

WINDOWS_APP_USER_MODEL_ID = "OreFiler.FileManager"


def _log_unhandled_exception(exc_type, exc, tb) -> None:
    # windowed exe では stderr が無く、未処理例外が見えないまま終了するためファイルに残す
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        log = session_file().with_name("error.log")
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as file:
            file.write(f"[{datetime.now().isoformat(timespec='seconds')}]\n{text}\n")
    except OSError:
        pass


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


def _capture_native_output() -> None:
    # Qt の致命的エラーや Python の fatal error は stderr に出るが、windowed exe では捨てられるためログへ向ける
    try:
        log_path = session_file().with_name("error.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stderr = log
    sys.stdout = log
    try:
        os.dup2(log.fileno(), 2)
    except OSError:
        pass
    faulthandler.enable(file=log, all_threads=True)


def main() -> int:
    if getattr(sys, "frozen", False):
        _capture_native_output()
    sys.excepthook = _log_unhandled_exception
    cleanup_old_exe()
    _set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    icon = QIcon(str(Path(__file__).with_name("icon.ico")))
    app.setWindowIcon(icon)
    session = load_session()
    history = session["history"]
    start_path = _session_start_path(history)
    left_path = Path(session["left_path"]).resolve() if session.get("left_path") and Path(session["left_path"]).is_dir() else start_path
    right_path = Path(session["right_path"]).resolve() if session.get("right_path") and Path(session["right_path"]).is_dir() else start_path
    window = MainWindow(
        left_path,
        right_path,
        history=history,
    )
    window.setWindowIcon(icon)
    window.restore_window_state(session.get("geometry", ""), session.get("splitter", ""))
    window.restore_font_size(session.get("font_size", 0))
    window.show()
    return app.exec()

if __name__ == "__main__":
    # 配布exeはこのファイルが起動スクリプトなので、SFTPワーカーの分岐もここで行う
    from ore_filer.services.remote_process import WORKER_ENV, _worker_main

    if os.environ.get(WORKER_ENV) == "1":
        _worker_main()
        raise SystemExit(0)
    raise SystemExit(main())