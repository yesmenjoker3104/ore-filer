import json
import os
import shutil
import sys
import tempfile
import urllib.request
import urllib.error
import zipfile

from PySide6.QtCore import QThread, Signal

from ore_filer.version import __version__

GITHUB_REPO = "yesmenjoker3104/ore-filer"
_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
ZIP_ASSET_NAME = "ore-filer-Windows.zip"
EXE_ASSET_NAME = "ore-filer.exe"
LAUNCHER_DIR_NAME = "app-launcher"
LAUNCHER_EXE_NAME = "app-launcher.exe"


def _parse_version(tag: str) -> tuple:
    return tuple(int(x) for x in tag.lstrip("v").split("."))


def _select_download_url(assets: list[dict]) -> str:
    """配布zip（app-launcher を含む）を優先し、無ければ ore-filer.exe を使う。"""
    by_name = {a.get("name", ""): a.get("browser_download_url", "") for a in assets}
    return by_name.get(ZIP_ASSET_NAME) or by_name.get(EXE_ASSET_NAME) or ""


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
                url = _select_download_url(data.get("assets", []))
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
    download_finished = Signal(str, str)  # new ore-filer.exe path, new app-launcher.exe path ("" if none)
    download_failed = Signal(str)

    def __init__(self, url: str, parent=None) -> None:
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        try:
            tmp_dir = tempfile.mkdtemp(prefix="ore_filer_update_")
            is_zip = self._url.lower().endswith(".zip")
            dest = os.path.join(tmp_dir, ZIP_ASSET_NAME if is_zip else EXE_ASSET_NAME)
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
            if is_zip:
                new_exe, new_launcher = _extract_release_zip(dest, tmp_dir)
            else:
                new_exe, new_launcher = dest, ""
            self.download_finished.emit(new_exe, new_launcher)
        except Exception as e:
            self.download_failed.emit(str(e))


def _extract_release_zip(zip_path: str, dest_dir: str) -> tuple[str, str]:
    """配布zipから ore-filer.exe と app-launcher.exe を取り出す。app-launcher が無ければ空文字を返す。"""
    launcher_entry = f"{LAUNCHER_DIR_NAME}/{LAUNCHER_EXE_NAME}"
    targets = {
        EXE_ASSET_NAME: os.path.join(dest_dir, EXE_ASSET_NAME),
        launcher_entry: os.path.join(dest_dir, LAUNCHER_EXE_NAME),
    }
    extracted: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if name not in targets or info.is_dir():
                continue
            with zf.open(info) as src, open(targets[name], "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted[name] = targets[name]
    if EXE_ASSET_NAME not in extracted:
        raise RuntimeError("配布zipに ore-filer.exe が含まれていません。")
    return extracted[EXE_ASSET_NAME], extracted.get(launcher_entry, "")


def _replace_file(new_path: str, target: str) -> None:
    """実行中でも置き換えられるよう、既存ファイルを .old にリネームしてから新しいファイルをコピーする。"""
    backup = target + ".old"
    if os.path.exists(target):
        if os.path.exists(backup):
            os.unlink(backup)
        os.rename(target, backup)
    try:
        shutil.copy2(new_path, target)
    except Exception:
        if os.path.exists(backup):
            os.rename(backup, target)
        raise


def install_update(new_exe_path: str, new_launcher_path: str = "") -> None:
    """ore-filer.exe と同梱の app-launcher\\app-launcher.exe を新しいものに置き換える。
    Windows では実行中 exe のリネームは可能なので bat 不要。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        if new_launcher_path:
            launcher_dir = os.path.join(os.path.dirname(sys.executable), LAUNCHER_DIR_NAME)
            os.makedirs(launcher_dir, exist_ok=True)
            _replace_file(new_launcher_path, os.path.join(launcher_dir, LAUNCHER_EXE_NAME))
        _replace_file(new_exe_path, sys.executable)
    finally:
        shutil.rmtree(os.path.dirname(new_exe_path), ignore_errors=True)


def cleanup_old_exe() -> None:
    """起動時に前回アップデートで残った .old ファイルを削除する。"""
    if not getattr(sys, "frozen", False):
        return
    launcher_old = os.path.join(
        os.path.dirname(sys.executable), LAUNCHER_DIR_NAME, LAUNCHER_EXE_NAME + ".old"
    )
    for old_path in (sys.executable + ".old", launcher_old):
        if os.path.exists(old_path):
            try:
                os.unlink(old_path)
            except Exception:
                pass
