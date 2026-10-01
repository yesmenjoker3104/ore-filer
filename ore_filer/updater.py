import json
import os
import subprocess
import sys
import tempfile
import urllib.request

from PySide6.QtCore import QThread, Signal

from ore_filer.version import __version__

GITHUB_REPO = "yesmenjoker3104/ore-filer"
_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _parse_version(tag: str) -> tuple:
    return tuple(int(x) for x in tag.lstrip("v").split("."))


class UpdateCheckThread(QThread):
    update_available = Signal(str, str)  # tag, download_url
    up_to_date = Signal()
    check_failed = Signal()

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                _API_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            tag = data.get("tag_name", "")
            if not tag:
                self.up_to_date.emit()
                return
            if _parse_version(tag) > _parse_version(__version__):
                url = ""
                for asset in data.get("assets", []):
                    if asset.get("name", "").endswith(".exe"):
                        url = asset["browser_download_url"]
                        break
                if url:
                    self.update_available.emit(tag, url)
                else:
                    self.up_to_date.emit()
            else:
                self.up_to_date.emit()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                self.up_to_date.emit()
            else:
                self.check_failed.emit()
        except Exception:
            self.check_failed.emit()


class UpdateDownloadThread(QThread):
    download_progress = Signal(int)   # 0–100
    download_finished = Signal(str)   # path to downloaded file
    download_failed = Signal(str)

    def __init__(self, url: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        try:
            tmp_dir = tempfile.mkdtemp(prefix="ore_filer_update_")
            dest = os.path.join(tmp_dir, "ore-filer.exe")
            with urllib.request.urlopen(self._url, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(131072)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            self.download_progress.emit(int(downloaded * 100 / total))
            self.download_finished.emit(dest)
        except Exception as e:
            self.download_failed.emit(str(e))


def install_update(new_exe_path: str) -> None:
    if not getattr(sys, "frozen", False):
        return
    current_exe = sys.executable
    bat_lines = [
        "@echo off",
        "timeout /t 2 /nobreak > nul",
        f'move /y "{new_exe_path}" "{current_exe}"',
        'del "%~f0"',
    ]
    bat_path = os.path.join(tempfile.gettempdir(), "ore_filer_update.bat")
    with open(bat_path, "w", encoding="cp932") as f:
        f.write("\r\n".join(bat_lines))
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=subprocess.CREATE_NO_WINDOW,
        close_fds=True,
    )
