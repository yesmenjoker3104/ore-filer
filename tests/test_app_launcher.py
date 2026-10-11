from __future__ import annotations

import pytest

from ore_filer.services.app_launcher import (
    AppLauncherError,
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
