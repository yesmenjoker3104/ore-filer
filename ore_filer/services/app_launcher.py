from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..settings import load_config

APP_LAUNCHER_EXE_NAME = "app-launcher.exe"
APP_LAUNCHER_DIR_NAME = "app-launcher"
_TIMEOUT_SECONDS = 30


class AppLauncherError(RuntimeError):
    pass


@dataclass(frozen=True)
class RegisteredApp:
    name: str
    path: str
    aliases: tuple[str, ...] = ()
    show_in_list: bool = True
    source: str = "manual"
    source_path: str | None = None


@dataclass(frozen=True)
class SourceFolder:
    path: str
    recursive: bool = False


def find_app_launcher() -> Path | None:
    """設定値、同梱先の順に app-launcher の実行ファイルを探す。"""
    candidates: list[Path] = []
    configured = load_config().get("app_launcher_path", "")
    if configured:
        candidates.append(Path(configured))

    # 配布時は ore-filer.exe と同じフォルダに同梱する想定
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parents[2]
    candidates.append(base / APP_LAUNCHER_DIR_NAME / APP_LAUNCHER_EXE_NAME)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _run_cli(exe: Path, args: list[str]) -> dict:
    """app-launcher を引数リストで実行し、JSON 応答を返す。失敗時は例外にする。"""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            [str(exe), *args],
            capture_output=True,
            text=True,
            creationflags=creationflags,
            timeout=_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AppLauncherError(f"app-launcherを実行できませんでした: {e}") from e

    try:
        data = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError as e:
        detail = proc.stderr.strip() or str(e)
        raise AppLauncherError(f"app-launcherの出力を読めませんでした: {detail}") from e

    if not data.get("ok"):
        raise AppLauncherError(data.get("error") or "app-launcherでエラーが発生しました。")
    return data


def load_registry(exe: Path) -> tuple[list[RegisteredApp], list[SourceFolder]]:
    data = _run_cli(exe, ["list"])
    apps = [
        RegisteredApp(
            name=app["name"],
            path=app["path"],
            aliases=tuple(app.get("aliases", [])),
            show_in_list=app.get("show_in_list", True),
            source=app.get("source", "manual"),
            source_path=app.get("source_path"),
        )
        for app in data.get("apps", [])
    ]
    sources = [
        SourceFolder(path=source["path"], recursive=source.get("recursive", False))
        for source in data.get("sources", [])
    ]
    return apps, sources


def list_registered_apps(exe: Path) -> list[RegisteredApp]:
    apps, _ = load_registry(exe)
    return apps


def open_with_registered_app(exe: Path, app_name: str, file_paths: Sequence[Path] = ()) -> None:
    args = ["open", "--application", app_name]
    args.extend(str(path) for path in file_paths)
    _run_cli(exe, args)


def register_application(exe: Path, path: Path, name: str | None = None) -> None:
    args = ["register", str(path)]
    if name:
        args += ["--name", name]
    _run_cli(exe, args)


def scan_application_directory(exe: Path, directory: Path, recursive: bool) -> dict:
    args = ["scan", str(directory)]
    if recursive:
        args.append("--recursive")
    return _run_cli(exe, args)


def unscan_application_directory(exe: Path, directory: Path) -> None:
    _run_cli(exe, ["unscan", str(directory)])


def update_application(
    exe: Path,
    target_path: str,
    new_name: str | None = None,
    new_path: Path | None = None,
    aliases: Sequence[str] | None = None,
    show_in_list: bool | None = None,
) -> None:
    """パスで対象を指定して更新する（同名アプリの取り違えを防ぐため）。"""
    args = ["update", target_path]
    if new_name is not None:
        args += ["--name", new_name]
    if new_path is not None:
        args += ["--path", str(new_path)]
    if aliases is not None:
        args += ["--aliases", ",".join(aliases)]
    if show_in_list is True:
        args.append("--show-in-list")
    elif show_in_list is False:
        args.append("--hide-from-list")
    _run_cli(exe, args)


def remove_application(exe: Path, path: str) -> None:
    _run_cli(exe, ["remove", path])
