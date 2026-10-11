"""remote_process.py のユニットテスト。

_worker はキューだけあれば動くため、queue.Queue でテストする。
RemoteSessionProcess は multiprocessing をモックして IPC ロジックだけを検証する。
"""
from __future__ import annotations

import queue
import threading
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ore_filer.services.remote_process import RemoteSessionProcess, _worker
from ore_filer.services.remote_service import RemoteEntry, RemoteError, RemoteProfile


def _profile() -> RemoteProfile:
    return RemoteProfile(name="t", host="127.0.0.1", port=2222, username="u")


# ── _worker テスト ───────────────────────────────────────────────


def _run_worker(reqs: list[dict]) -> list[dict]:
    """_worker に命令リストを送り、得られたレスポンスを返す。"""
    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    for r in reqs:
        req_q.put(r)
    thread = threading.Thread(target=_worker, args=(req_q, resp_q), daemon=True)
    thread.start()
    thread.join(timeout=5)
    responses: list[dict] = []
    while not resp_q.empty():
        responses.append(resp_q.get_nowait())
    return responses


class FakeSession:
    """_worker から呼ばれる RemoteSession の差し替え。"""

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


def _patch_session():
    # _worker は関数内で remote_service.RemoteSession をインポートするため
    # そちらのモジュールをパッチする
    return patch("ore_filer.services.remote_service.RemoteSession", FakeSession)


def test_worker_connect_then_close():
    with _patch_session():
        resps = _run_worker([
            {"cmd": "connect", "profile": asdict(_profile()), "password": None},
            {"cmd": "close"},
        ])
    assert resps[0] == {"ok": True}
    assert resps[1] == {"ok": True}


def test_worker_home_dir():
    with _patch_session():
        resps = _run_worker([
            {"cmd": "connect", "profile": asdict(_profile()), "password": None},
            {"cmd": "home_dir"},
            {"cmd": "close"},
        ])
    assert resps[1] == {"ok": True, "data": "/home/user"}


def test_worker_list_dir():
    with _patch_session():
        resps = _run_worker([
            {"cmd": "connect", "profile": asdict(_profile()), "password": None},
            {"cmd": "list_dir", "path": "/tmp"},
            {"cmd": "close"},
        ])
    entries = resps[1]["data"]
    assert resps[1]["ok"]
    assert entries[0]["name"] == "a.txt"


def test_worker_command_before_connect_returns_error():
    with _patch_session():
        resps = _run_worker([
            {"cmd": "home_dir"},
            {"cmd": "close"},
        ])
    assert resps[0]["ok"] is False
    assert "未接続" in resps[0]["error"]


def test_worker_unknown_command():
    with _patch_session():
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

    with patch("ore_filer.services.remote_service.RemoteSession", bad_session):
        resps = _run_worker([
            {"cmd": "connect", "profile": asdict(_profile()), "password": None},
        ])
    assert resps[0]["ok"] is False
    assert "port closed" in resps[0]["error"]


# ── RemoteSessionProcess ヘルパーメソッドのテスト ────────────────


def _make_session_process(req_q, resp_q) -> RemoteSessionProcess:
    """__init__ を通らずに内部キューだけをセットした半モック。"""
    obj = object.__new__(RemoteSessionProcess)
    obj._req_q = req_q
    obj._resp_q = resp_q
    proc = MagicMock()
    proc.is_alive.return_value = True
    obj._proc = proc
    return obj


def test_require_raises_on_not_ok():
    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    sp = _make_session_process(req_q, resp_q)
    with pytest.raises(RemoteError, match="boom"):
        sp._require({"ok": False, "error": "boom"})


def test_require_returns_resp_on_ok():
    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    sp = _make_session_process(req_q, resp_q)
    result = sp._require({"ok": True, "data": 42})
    assert result["data"] == 42


def test_call_timeout_raises_remote_error():
    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()  # 何も入れない
    sp = _make_session_process(req_q, resp_q)
    with pytest.raises(RemoteError, match="タイムアウト"):
        sp._call({"cmd": "home_dir"}, timeout=0.1)


def test_call_dead_process_raises_remote_error():
    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    sp = _make_session_process(req_q, resp_q)
    sp._proc.is_alive.return_value = False
    with pytest.raises(RemoteError, match="応答していません"):
        sp._call({"cmd": "home_dir"})


# ── download_entries / upload_paths のロジックテスト ────────────


def test_download_entries_calls_download_for_each_file():
    downloaded: list[str] = []

    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    sp = _make_session_process(req_q, resp_q)
    sp.download = lambda remote, local: downloaded.append(remote)

    entries = [
        RemoteEntry(name="a.txt", path="/r/a.txt", is_dir=False, size=10, mtime=0),
        RemoteEntry(name="b.txt", path="/r/b.txt", is_dir=False, size=20, mtime=0),
    ]
    sp.download_entries(entries, Path("/tmp/dst"))

    assert downloaded == ["/r/a.txt", "/r/b.txt"]


def test_download_entries_reports_progress():
    progress: list[tuple[int, int]] = []

    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    sp = _make_session_process(req_q, resp_q)
    sp.download = lambda *_: None

    entries = [
        RemoteEntry(name="x", path="/x", is_dir=False, size=1, mtime=0),
        RemoteEntry(name="y", path="/y", is_dir=False, size=1, mtime=0),
    ]
    sp.download_entries(entries, Path("/tmp"), progress_cb=lambda d, t: progress.append((d, t)))

    assert progress == [(1, 2), (2, 2)]


def test_upload_paths_calls_upload_for_each_file(tmp_path):
    uploaded: list[str] = []

    req_q: queue.Queue = queue.Queue()
    resp_q: queue.Queue = queue.Queue()
    sp = _make_session_process(req_q, resp_q)
    sp.upload = lambda local, remote: uploaded.append(remote)

    f1 = tmp_path / "a.txt"
    f1.write_text("a")
    f2 = tmp_path / "b.txt"
    f2.write_text("b")

    sp.upload_paths([f1, f2], "/remote")

    assert uploaded == ["/remote/a.txt", "/remote/b.txt"]
