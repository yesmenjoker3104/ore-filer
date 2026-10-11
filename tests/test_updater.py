from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

from ore_filer.updater import (
    _extract_release_zip,
    _select_download_url,
    cleanup_old_exe,
    install_update,
)


def _asset(name: str) -> dict:
    return {"name": name, "browser_download_url": f"https://example.test/{name}"}


def test_select_download_url_prefers_release_zip() -> None:
    assets = [_asset("ore-filer.exe"), _asset("ore-filer-Windows.zip")]
    assert _select_download_url(assets) == "https://example.test/ore-filer-Windows.zip"


def test_select_download_url_falls_back_to_exe() -> None:
    assets = [_asset("app-launcher.exe"), _asset("ore-filer.exe")]
    assert _select_download_url(assets) == "https://example.test/ore-filer.exe"


def test_select_download_url_empty_when_no_known_asset() -> None:
    assert _select_download_url([_asset("other.exe")]) == ""


def _make_zip(path: Path, members: dict[str, bytes]) -> str:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return str(path)


def test_extract_release_zip_returns_both_exes(tmp_path: Path) -> None:
    zip_path = _make_zip(
        tmp_path / "ore-filer-Windows.zip",
        {"ore-filer.exe": b"new-ore", "app-launcher\\app-launcher.exe": b"new-launcher"},
    )
    out = tmp_path / "out"
    out.mkdir()

    exe, launcher = _extract_release_zip(zip_path, str(out))

    assert Path(exe).read_bytes() == b"new-ore"
    assert Path(launcher).read_bytes() == b"new-launcher"


def test_extract_release_zip_without_launcher(tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path / "ore-filer-Windows.zip", {"ore-filer.exe": b"new-ore"})
    out = tmp_path / "out"
    out.mkdir()

    exe, launcher = _extract_release_zip(zip_path, str(out))

    assert Path(exe).read_bytes() == b"new-ore"
    assert launcher == ""


def test_extract_release_zip_requires_ore_filer_exe(tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path / "ore-filer-Windows.zip", {"readme.txt": b"x"})
    out = tmp_path / "out"
    out.mkdir()

    with pytest.raises(RuntimeError):
        _extract_release_zip(zip_path, str(out))


@pytest.fixture
def frozen_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    current = install_dir / "ore-filer.exe"
    current.write_bytes(b"old-ore")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(current))
    return install_dir


def _new_files(tmp_path: Path) -> tuple[str, str]:
    tmp = tmp_path / "download"
    tmp.mkdir()
    new_exe = tmp / "ore-filer.exe"
    new_exe.write_bytes(b"new-ore")
    new_launcher = tmp / "app-launcher.exe"
    new_launcher.write_bytes(b"new-launcher")
    return str(new_exe), str(new_launcher)


def test_install_update_replaces_launcher_in_existing_folder(
    frozen_install: Path, tmp_path: Path
) -> None:
    launcher_dir = frozen_install / "app-launcher"
    launcher_dir.mkdir()
    (launcher_dir / "app-launcher.exe").write_bytes(b"old-launcher")
    new_exe, new_launcher = _new_files(tmp_path)

    install_update(new_exe, new_launcher)

    assert (frozen_install / "ore-filer.exe").read_bytes() == b"new-ore"
    assert (launcher_dir / "app-launcher.exe").read_bytes() == b"new-launcher"


def test_install_update_creates_launcher_folder_when_missing(
    frozen_install: Path, tmp_path: Path
) -> None:
    new_exe, new_launcher = _new_files(tmp_path)

    install_update(new_exe, new_launcher)

    assert (frozen_install / "app-launcher" / "app-launcher.exe").read_bytes() == b"new-launcher"


def test_install_update_without_launcher_keeps_existing_launcher(
    frozen_install: Path, tmp_path: Path
) -> None:
    launcher_dir = frozen_install / "app-launcher"
    launcher_dir.mkdir()
    (launcher_dir / "app-launcher.exe").write_bytes(b"old-launcher")
    new_exe, _ = _new_files(tmp_path)

    install_update(new_exe, "")

    assert (frozen_install / "ore-filer.exe").read_bytes() == b"new-ore"
    assert (launcher_dir / "app-launcher.exe").read_bytes() == b"old-launcher"


def test_cleanup_old_exe_removes_both_backups(frozen_install: Path, tmp_path: Path) -> None:
    launcher_dir = frozen_install / "app-launcher"
    launcher_dir.mkdir()
    old_ore = frozen_install / "ore-filer.exe.old"
    old_launcher = launcher_dir / "app-launcher.exe.old"
    old_ore.write_bytes(b"x")
    old_launcher.write_bytes(b"y")

    cleanup_old_exe()

    assert not old_ore.exists()
    assert not old_launcher.exists()
