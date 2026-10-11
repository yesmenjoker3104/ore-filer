"""paramiko を子プロセスで動かす SFTP セッションアダプタ。

multiprocessing.spawn は PyInstaller frozen exe で誤動作するため、
subprocess.Popen + 環境変数 + stdin/stdout JSON IPC で代替する。

起動フロー:
  親プロセス(GUI)が ORE_FILER_SFTP_WORKER=1 の環境変数付きで同じ exe を起動
  → __main__.py が env を見て _worker_main() を呼んで GUI は起動しない
  → 親子間は改行区切り JSON でコマンド/応答を交換する
"""
from __future__ import annotations

import contextlib
import json
import os
import posixpath
import queue
import stat as stat_module
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from ore_filer.services.remote_service import RemoteEntry, RemoteError, RemoteProfile

WORKER_ENV = "ORE_FILER_SFTP_WORKER"


# ── 子プロセス側 ──────────────────────────────────────────────


def _send(stream, obj: dict) -> None:
    stream.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
    stream.flush()


def _worker_main(stdin=None, stdout=None) -> None:
    """子プロセスのメインループ。stdin から JSON 命令を受け取り stdout に応答する。"""
    from ore_filer.services.remote_service import RemoteSession

    if stdin is None:
        stdin = sys.stdin.buffer
    if stdout is None:
        stdout = sys.stdout.buffer

    session = None

    while True:
        line = stdin.readline()
        if not line:
            break
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        cmd = req.get("cmd")
        try:
            if cmd == "connect":
                profile = RemoteProfile(**req["profile"])
                session = RemoteSession(profile, password=req.get("password"))
                _send(stdout, {"ok": True})

            elif session is None:
                _send(stdout, {"ok": False, "error": "未接続です"})

            elif cmd == "home_dir":
                _send(stdout, {"ok": True, "data": session.home_dir()})

            elif cmd == "list_dir":
                entries = session.list_dir(req["path"])
                _send(stdout, {"ok": True, "data": [asdict(e) for e in entries]})

            elif cmd == "download":
                session.download(req["remote"], Path(req["local"]))
                _send(stdout, {"ok": True})

            elif cmd == "upload":
                session.upload(Path(req["local"]), req["remote"])
                _send(stdout, {"ok": True})

            elif cmd == "mkdir_remote":
                remote_path = req["path"]
                try:
                    session._sftp.mkdir(remote_path)
                except OSError:
                    try:
                        mode = session._sftp.stat(remote_path).st_mode or 0
                        if not stat_module.S_ISDIR(mode):
                            raise RemoteError(
                                f"同名のファイルがあるためフォルダを作成できません: {remote_path}"
                            )
                    except RemoteError:
                        raise
                    except OSError as e:
                        raise RemoteError(
                            f"フォルダを作成できませんでした: {remote_path} ({e})"
                        ) from e
                _send(stdout, {"ok": True})

            elif cmd == "close":
                if session:
                    session.close()
                _send(stdout, {"ok": True})
                break

            else:
                _send(stdout, {"ok": False, "error": f"不明なコマンド: {cmd}"})

        except Exception as e:  # noqa: BLE001
            _send(stdout, {"ok": False, "error": str(e) or type(e).__name__})

    if session:
        with contextlib.suppress(Exception):
            session.close()


# ── メインプロセス側 ──────────────────────────────────────────


class RemoteSessionProcess:
    """RemoteSession と同じインタフェースを持つ子プロセス版セッション。

    subprocess.Popen + stdin/stdout JSON IPC で paramiko を同一プロセスから
    分離することで SIGABRT を回避する。
    """

    def __init__(self, profile: RemoteProfile, password: str | None = None) -> None:
        env = os.environ.copy()
        env[WORKER_ENV] = "1"

        # frozen exe はそのまま起動、開発時は -m ore_filer で __main__.py を通す
        if getattr(sys, "frozen", False):
            cmd = [sys.executable]
        else:
            cmd = [sys.executable, "-m", "ore_filer"]

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        self._resp_q: queue.Queue = queue.Queue()
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            daemon=True,
            name="sftp-reader",
        )
        self._reader_thread.start()
        self.profile = profile

        try:
            resp = self._call(
                {"cmd": "connect", "profile": asdict(profile), "password": password},
                timeout=35,
            )
        except Exception:
            self._kill()
            raise
        if not resp.get("ok"):
            self._kill()
            raise RemoteError(resp.get("error", "接続に失敗しました"))

    # ── IPC ────────────────────────────────────────────────────

    def _read_loop(self) -> None:
        while True:
            try:
                line = self._proc.stdout.readline()
            except Exception:  # noqa: BLE001
                break
            if not line:
                self._resp_q.put(None)  # EOF = プロセス終了
                break
            try:
                self._resp_q.put(json.loads(line))
            except json.JSONDecodeError:
                pass

    def _call(self, req: dict, timeout: float = 30.0) -> dict:
        if self._proc.poll() is not None:
            raise RemoteError("リモートプロセスが終了しています")
        line = (json.dumps(req, ensure_ascii=False) + "\n").encode()
        self._proc.stdin.write(line)
        self._proc.stdin.flush()
        try:
            resp = self._resp_q.get(timeout=timeout)
        except queue.Empty:
            raise RemoteError("タイムアウトしました")
        if resp is None:
            raise RemoteError("プロセスが予期せず終了しました")
        return resp

    def _require(self, resp: dict) -> dict:
        if not resp.get("ok"):
            raise RemoteError(resp.get("error", "不明なエラー"))
        return resp

    def _kill(self) -> None:
        with contextlib.suppress(Exception):
            self._proc.kill()
            self._proc.wait(timeout=3)

    # ── RemoteSession 互換インタフェース ────────────────────────

    def home_dir(self) -> str:
        return self._require(self._call({"cmd": "home_dir"}, timeout=10))["data"]

    def list_dir(self, path: str) -> list[RemoteEntry]:
        resp = self._require(self._call({"cmd": "list_dir", "path": path}))
        return [RemoteEntry(**d) for d in resp["data"]]

    def download(self, remote_path: str, local_path: Path) -> None:
        self._require(self._call(
            {"cmd": "download", "remote": remote_path, "local": str(local_path)},
            timeout=120,
        ))

    def upload(self, local_path: Path, remote_path: str) -> None:
        self._require(self._call(
            {"cmd": "upload", "local": str(local_path), "remote": remote_path},
            timeout=120,
        ))

    def download_entries(
        self,
        entries: list[RemoteEntry],
        local_dir: Path,
        progress_cb=None,
    ) -> None:
        for done, entry in enumerate(entries, start=1):
            self._download_entry(entry, local_dir)
            if progress_cb is not None:
                progress_cb(done, len(entries))

    def _download_entry(self, entry: RemoteEntry, local_dir: Path) -> None:
        target = local_dir / entry.name
        if not entry.is_dir:
            self.download(entry.path, target)
            return
        try:
            target.mkdir(exist_ok=True)
        except OSError as e:
            raise RemoteError(f"フォルダを作成できませんでした: {target} ({e})") from e
        for child in self.list_dir(entry.path):
            self._download_entry(child, target)

    def upload_paths(
        self,
        local_paths: list[Path],
        remote_dir: str,
        progress_cb=None,
    ) -> None:
        for done, local_path in enumerate(local_paths, start=1):
            self._upload_path(local_path, remote_dir)
            if progress_cb is not None:
                progress_cb(done, len(local_paths))

    def _upload_path(self, local_path: Path, remote_dir: str) -> None:
        remote_path = posixpath.join(remote_dir, local_path.name)
        if not local_path.is_dir():
            self.upload(local_path, remote_path)
            return
        self._ensure_remote_dir(remote_path)
        for child in sorted(local_path.iterdir()):
            self._upload_path(child, remote_path)

    def _ensure_remote_dir(self, remote_path: str) -> None:
        self._require(self._call({"cmd": "mkdir_remote", "path": remote_path}))

    def close(self) -> None:
        try:
            with contextlib.suppress(Exception):
                line = (json.dumps({"cmd": "close"}, ensure_ascii=False) + "\n").encode()
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
                self._resp_q.get(timeout=5)
        finally:
            self._kill()
