from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ore_filer.services.app_launcher import (
    AppLauncherError,
    _run_cli,
    find_app_launcher,
    list_registered_apps,
    open_with_registered_app,
)

exe = find_app_launcher()
requires_exe = pytest.mark.skipif(exe is None, reason="app-launcher.exe が見つかりません")


@requires_exe
def test_lists_registered_apps_from_real_cli() -> None:
    apps = list_registered_apps(exe)
    assert all(app.name and app.path for app in apps)


@requires_exe
def test_open_unknown_application_raises_error() -> None:
    with pytest.raises(AppLauncherError):
        open_with_registered_app(exe, "存在しないアプリ-xyz-123")


JAPANESE_PATH = r"C:\Users\tester\OneDrive\ドキュメント\My Games\Game.exe"
# 旧 app-launcher と同じく ensure_ascii=False で UTF-8 の日本語を出力する子プロセス
ECHO_JSON_SCRIPT = "import json, sys; print(json.dumps({'ok': True, 'path': sys.argv[1]}, ensure_ascii=False))"


@pytest.mark.parametrize("parent_stdio_encoding", ["utf-8", "cp932"])
def test_run_cli_decodes_utf8_json_regardless_of_parent_encoding(
    monkeypatch: pytest.MonkeyPatch, parent_stdio_encoding: str
) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", parent_stdio_encoding)
    data = _run_cli(Path(sys.executable), ["-c", ECHO_JSON_SCRIPT, JAPANESE_PATH])
    assert data["path"] == JAPANESE_PATH


def test_run_cli_decodes_utf8_stderr_in_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    script = "import sys; sys.stderr.write(sys.argv[1]); print('not json')"
    with pytest.raises(AppLauncherError) as excinfo:
        _run_cli(Path(sys.executable), ["-c", script, JAPANESE_PATH])
    assert JAPANESE_PATH in str(excinfo.value)
