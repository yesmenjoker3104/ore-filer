"""remote_process.py のユニットテスト。

_worker_main は stdin/stdout の BytesIO を差し込んでテストする。
RemoteSessionProcess は subprocess.Popen をモックして IPC ロジックを検証する。
"""
from __future__ import annotations

import io
import json
import queue
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ore_filer.services.remote_process import (
    RemoteSessionProcess,
    _worker_main,
)
from ore_filer.services.remote_service import RemoteEntry, RemoteError, RemoteProfile


def _profile() -> RemoteProfile:
    return RemoteProfile(name="t", host="127.0.0.1", port=2222, username="u")


# ── _worker_main テスト ──────────────────────────────────────────


class FakeRemoteSession:
    def __init__(self, profile, password=None):
        self._sftp = MagicMock()

    def home_dir(self) -> str:
        return "/home/user"

    def list_dir(self, path: str) -> list[RemoteEntry]:
        return [RemoteEntry(name="a.txt", path=f"{path}/a.txt", is_dir=False, size=100, mtime=0)]

    def download(self, remote_path: str, local_path: Path) -> None:
        pass

    def upload(self, local_path: Path, remote_path: str) -> None:
        pass

    def close(self) -> None:
        pass


def _run_worker(commands: list[dict]) -> list[dict]:
    """_worker_main に命令リストを送り、応答リストを返す。"""
    stdin = io.BytesIO(b"".join((json.dumps(c) + "\n").encode() for c in commands))
    stdout = io.BytesIO()
    with patch("ore_filer.services.remote_service.RemoteSession", FakeRemoteSession):
        _worker_main(stdin, stdout)
    stdout.seek(0)
    return [json.loads(line) for line in stdout.readlines()]


def test_worker_connect_then_close():
    resps = _run_worker([
        {"cmd": "connect", "profile": asdict(_profile()), "password": None},
        {"cmd": "close"},
    ])
    assert resps[0] == {"ok": True}
    assert resps[1] == {"ok": True}


def test_worker_home_dir():
    resps = _run_worker([
        {"cmd": "connect", "profile": asdict(_profile()), "password": None},
        {"cmd": "home_dir"},
        {"cmd": "close"},
    ])
    assert resps[1] == {"ok": True, "data": "/home/user"}


def test_worker_list_dir():
    resps = _run_worker([
        {"cmd": "connect", "profile": asdict(_profile()), "password": None},
        {"cmd": "list_dir", "path": "/tmp"},
        {"cmd": "close"},
    ])
    assert resps[1]["ok"]
    assert resps[1]["data"][0]["name"] == "a.txt"


def test_worker_command_before_connect_returns_error():
    resps = _run_worker([
        {"cmd": "home_dir"},
        {"cmd": "close"},
    ])
    assert resps[0]["ok"] is False
    assert "未接続" in resps[0]["error"]


def test_worker_unknown_command():
    resps = _run_worker([
        {"cmd": "connect", "profile": asdict(_profile()), "password": None},
        {"cmd": "magic"},
        {"cmd": "close"},
    ])
    assert resps[1]["ok"] is False
    assert "magic" in resps[1]["error"]


def test_worker_connect_failure_propagates():
    def bad_session(*_args, **_kwargs):
        raise ConnectionRefusedError("port closed")

    stdin = io.BytesIO((json.dumps(
        {"cmd": "connect", "profile": asdict(_profile()), "password": None}
    ) + "\n").encode())
    stdout = io.BytesIO()
    with patch("ore_filer.services.remote_service.RemoteSession", bad_session):
        _worker_main(stdin, stdout)
    stdout.seek(0)
    resp = json.loads(stdout.readline())
    assert resp["ok"] is False
    assert "port closed" in resp["error"]


def test_worker_exits_on_eof():
    stdin = io.BytesIO(b"")  # 即 EOF
    stdout = io.BytesIO()
    with patch("ore_filer.services.remote_service.RemoteSession", FakeRemoteSession):
        _worker_main(stdin, stdout)  # ハングせず返ること


# ── RemoteSessionProcess ヘルパーメソッドのテスト ────────────────


def _make_session_process(proc_mock=None) -> RemoteSessionProcess:
    """__init__ を通らずに内部状態だけをセットした半モック。"""
    obj = object.__new__(RemoteSessionProcess)
    obj._resp_q = queue.Queue()
    if proc_mock is None:
        proc_mock = MagicMock()
        proc_mock.poll.return_value = None
    obj._proc = proc_mock
    return obj


def test_require_raises_on_not_ok():
    sp = _make_session_process()
    with pytest.raises(RemoteError, match="boom"):
        sp._require({"ok": False, "error": "boom"})


def test_require_returns_resp_on_ok():
    sp = _make_session_process()
    result = sp._require({"ok": True, "data": 42})
    assert result["data"] == 42


def test_call_timeout_raises_remote_error():
    sp = _make_session_process()
    proc = sp._proc
    proc.stdin = MagicMock()
    with pytest.raises(RemoteError, match="タイムアウト"):
        sp._call({"cmd": "home_dir"}, timeout=0.1)


def test_call_dead_process_raises_remote_error():
    proc_mock = MagicMock()
    proc_mock.poll.return_value = 1  # プロセス終了済み
    sp = _make_session_process(proc_mock)
    with pytest.raises(RemoteError, match="終了"):
        sp._call({"cmd": "home_dir"})


def test_call_eof_raises_remote_error():
    sp = _make_session_process()
    sp._resp_q.put(None)  # EOF シグナル
    sp._proc.stdin = MagicMock()
    with pytest.raises(RemoteError, match="予期せず終了"):
        sp._call({"cmd": "home_dir"}, timeout=1.0)


# ── download_entries / upload_paths のロジックテスト ────────────


def test_download_entries_calls_download_for_each_file():
    downloaded: list[str] = []
    sp = _make_session_process()
    sp.download = lambda remote, local: downloaded.append(remote)

    entries = [
        RemoteEntry(name="a.txt", path="/r/a.txt", is_dir=False, size=10, mtime=0),
        RemoteEntry(name="b.txt", path="/r/b.txt", is_dir=False, size=20, mtime=0),
    ]
    sp.download_entries(entries, Path("/tmp/dst"))

    assert downloaded == ["/r/a.txt", "/r/b.txt"]


def test_download_entries_reports_progress():
    progress: list[tuple[int, int]] = []
    sp = _make_session_process()
    sp.download = lambda *_: None

    entries = [
        RemoteEntry(name="x", path="/x", is_dir=False, size=1, mtime=0),
        RemoteEntry(name="y", path="/y", is_dir=False, size=1, mtime=0),
    ]
    sp.download_entries(entries, Path("/tmp"), progress_cb=lambda d, t: progress.append((d, t)))

    assert progress == [(1, 2), (2, 2)]


def test_upload_paths_calls_upload_for_each_file(tmp_path):
    uploaded: list[str] = []
    sp = _make_session_process()
    sp.upload = lambda local, remote: uploaded.append(remote)

    f1 = tmp_path / "a.txt"
    f1.write_text("a")
    f2 = tmp_path / "b.txt"
    f2.write_text("b")

    sp.upload_paths([f1, f2], "/remote")

    assert uploaded == ["/remote/a.txt", "/remote/b.txt"]
