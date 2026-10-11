"""SSH 経由のリモートファイルアクセス（SFTP / SCP 転送）。

一覧取得は SFTP を使う。SCP プロトコル単体ではディレクトリの列挙ができないため、
同じ SSH 接続上で SFTP を併用する。
"""
from __future__ import annotations

import posixpath
import stat as stat_module
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import paramiko


class RemoteError(RuntimeError):
    pass


@dataclass(frozen=True)
class RemoteProfile:
    name: str
    host: str
    username: str
    port: int = 22
    key_filename: str | None = None
    initial_path: str = "/"


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    mtime: float


class RemoteSession:
    """1 つの SSH 接続と SFTP チャネルを保持する。"""

    def __init__(self, profile: RemoteProfile, password: str | None = None) -> None:
        self.profile = profile
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            self._client.load_system_host_keys()
            self._client.connect(
                hostname=profile.host,
                port=profile.port,
                username=profile.username,
                password=password,
                key_filename=profile.key_filename,
                timeout=15,
                banner_timeout=15,
                auth_timeout=30,
                look_for_keys=profile.key_filename is None,
                allow_agent=True,
            )
            self._sftp = self._client.open_sftp()
        except (paramiko.SSHException, OSError) as e:
            self._client.close()
            raise RemoteError(f"接続できませんでした: {e}") from e

    def list_dir(self, path: str) -> list[RemoteEntry]:
        try:
            attrs = self._sftp.listdir_attr(path)
        except OSError as e:
            raise RemoteError(f"フォルダを開けませんでした: {path} ({e})") from e

        entries: list[RemoteEntry] = []
        for attr in attrs:
            name = attr.filename
            if name in (".", ".."):
                continue
            mode = attr.st_mode or 0
            entries.append(
                RemoteEntry(
                    name=name,
                    path=posixpath.join(path, name),
                    is_dir=stat_module.S_ISDIR(mode),
                    size=attr.st_size or 0,
                    mtime=float(attr.st_mtime or 0),
                )
            )
        entries.sort(key=lambda entry: (not entry.is_dir, entry.name.casefold()))
        return entries

    def home_dir(self) -> str:
        try:
            return self._sftp.normalize(".")
        except OSError as e:
            raise RemoteError(f"ホームフォルダを取得できませんでした: {e}") from e

    def parent_dir(self, path: str) -> str:
        return posixpath.dirname(path.rstrip("/")) or "/"

    def download(self, remote_path: str, local_path: Path) -> None:
        try:
            self._sftp.get(remote_path, str(local_path))
        except OSError as e:
            raise RemoteError(f"ダウンロードできませんでした: {remote_path} ({e})") from e

    def upload(self, local_path: Path, remote_path: str) -> None:
        try:
            self._sftp.put(str(local_path), remote_path)
        except OSError as e:
            raise RemoteError(f"アップロードできませんでした: {local_path} ({e})") from e

    def download_entries(
        self,
        entries: list[RemoteEntry],
        local_dir: Path,
        progress_cb: Callable[[int, int], None] | None = None,
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
        progress_cb: Callable[[int, int], None] | None = None,
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
        try:
            self._sftp.mkdir(remote_path)
        except OSError:
            try:
                is_dir = stat_module.S_ISDIR(self._sftp.stat(remote_path).st_mode or 0)
            except OSError as e:
                raise RemoteError(f"フォルダを作成できませんでした: {remote_path} ({e})") from e
            if not is_dir:
                raise RemoteError(f"同名のファイルがあるためフォルダを作成できません: {remote_path}")

    def close(self) -> None:
        try:
            self._sftp.close()
        finally:
            self._client.close()
