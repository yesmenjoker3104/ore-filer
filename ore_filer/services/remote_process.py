"""paramiko を子プロセスで動かす SFTP セッションアダプタ。

同一プロセスで Qt と paramiko の Transport スレッドを共存させると
OpenSSL が SIGABRT するため、SFTP 処理全体を spawn した子プロセスに分離する。
子プロセスは Qt を一切インポートせず、IPC（multiprocessing.Queue）でのみ通信する。
"""
from __future__ import annotations

import multiprocessing
import posixpath
import queue
import stat as stat_module
from dataclasses import asdict
from pathlib import Path

from ore_filer.services.remote_service import RemoteEntry, RemoteError, RemoteProfile


# ── 子プロセス側 ──────────────────────────────────────────────


def _worker(
    req_queue: multiprocessing.Queue,
    resp_queue: multiprocessing.Queue,
) -> None:
    """子プロセスのメインループ。SSH セッションを保持し IPC 命令を処理する。"""
    from ore_filer.services.remote_service import RemoteSession

    session: RemoteSession | None = None

    while True:
        try:
            req = req_queue.get(timeout=600)
        except Exception:
            break

        cmd = req.get("cmd")
        try:
            if cmd == "connect":
                profile = RemoteProfile(**req["profile"])
                session = RemoteSession(profile, password=req.get("password"))
                resp_queue.put({"ok": True})

            elif session is None:
                resp_queue.put({"ok": False, "error": "未接続です"})

            elif cmd == "home_dir":
                resp_queue.put({"ok": True, "data": session.home_dir()})

            elif cmd == "list_dir":
                entries = session.list_dir(req["path"])
                resp_queue.put({"ok": True, "data": [asdict(e) for e in entries]})

            elif cmd == "download":
                session.download(req["remote"], Path(req["local"]))
                resp_queue.put({"ok": True})

            elif cmd == "upload":
                session.upload(Path(req["local"]), req["remote"])
                resp_queue.put({"ok": True})

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
                resp_queue.put({"ok": True})

            elif cmd == "close":
                session.close()
                resp_queue.put({"ok": True})
                break

            else:
                resp_queue.put({"ok": False, "error": f"不明なコマンド: {cmd}"})

        except Exception as e:  # noqa: BLE001
            resp_queue.put({"ok": False, "error": str(e) or type(e).__name__})


# ── メインプロセス側 ──────────────────────────────────────────


class RemoteSessionProcess:
    """RemoteSession と同じインタフェースを持つ子プロセス版セッション。

    子プロセスで paramiko を動かし、Qt と Transport スレッドを同一プロセスから
    分離することで SIGABRT を回避する。
    """

    def __init__(self, profile: RemoteProfile, password: str | None = None) -> None:
        ctx = multiprocessing.get_context("spawn")
        self._req_q: multiprocessing.Queue = ctx.Queue()
        self._resp_q: multiprocessing.Queue = ctx.Queue()
        self._proc = ctx.Process(
            target=_worker,
            args=(self._req_q, self._resp_q),
            daemon=True,
        )
        self._proc.start()
        self.profile = profile

        try:
            resp = self._call(
                {"cmd": "connect", "profile": asdict(profile), "password": password},
                timeout=35,
            )
        except Exception:
            self._proc.terminate()
            self._proc.join(timeout=3)
            raise
        if not resp.get("ok"):
            self._proc.terminate()
            self._proc.join(timeout=3)
            raise RemoteError(resp.get("error", "接続に失敗しました"))

    # ── IPC ────────────────────────────────────────────────────

    def _call(self, req: dict, timeout: float = 30.0) -> dict:
        if not self._proc.is_alive():
            raise RemoteError("リモートプロセスが応答していません")
        self._req_q.put(req)
        try:
            return self._resp_q.get(timeout=timeout)
        except queue.Empty as e:
            raise RemoteError("タイムアウトしました") from e

    def _require(self, resp: dict) -> dict:
        if not resp.get("ok"):
            raise RemoteError(resp.get("error", "不明なエラー"))
        return resp

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
            self._req_q.put({"cmd": "close"})
            self._resp_q.get(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        finally:
            if self._proc.is_alive():
                self._proc.terminate()
            self._proc.join(timeout=3)
