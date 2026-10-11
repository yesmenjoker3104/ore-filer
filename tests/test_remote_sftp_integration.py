"""実 SFTP サーバーに対する結合テスト。

次の環境変数がすべて設定されているときだけ実行される（それ以外はスキップ）。

- ORE_FILER_TEST_SFTP_HOST
- ORE_FILER_TEST_SFTP_PORT（省略時 22）
- ORE_FILER_TEST_SFTP_USER
- ORE_FILER_TEST_SFTP_PASSWORD

接続先のホスト鍵は既定の known_hosts に登録されている必要がある。
テストはホーム直下に一時フォルダを作り、終了時に削除する。
"""
from __future__ import annotations

import os
import posixpath
import stat
import tempfile
import unittest
import uuid
from pathlib import Path

from ore_filer.services.remote_service import RemoteError, RemoteProfile, RemoteSession

HOST = os.environ.get("ORE_FILER_TEST_SFTP_HOST")
PORT = int(os.environ.get("ORE_FILER_TEST_SFTP_PORT", "22"))
USER = os.environ.get("ORE_FILER_TEST_SFTP_USER")
PASSWORD = os.environ.get("ORE_FILER_TEST_SFTP_PASSWORD")
ENABLED = all((HOST, USER, PASSWORD))


def _remove_tree(sftp, path: str) -> None:
    for attr in sftp.listdir_attr(path):
        child = posixpath.join(path, attr.filename)
        if stat.S_ISDIR(attr.st_mode or 0):
            _remove_tree(sftp, child)
        else:
            sftp.remove(child)
    sftp.rmdir(path)


@unittest.skipUnless(ENABLED, "ORE_FILER_TEST_SFTP_* が未設定のためスキップ")
class RemoteSftpIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = RemoteProfile(
            name="integration", host=HOST, username=USER, port=PORT
        )
        self.session = RemoteSession(self.profile, PASSWORD)
        self.remote_root = posixpath.join(
            self.session.home_dir(), f"ore-filer-test-{uuid.uuid4().hex[:8]}"
        )
        self.session._sftp.mkdir(self.remote_root)
        self._tmp = tempfile.TemporaryDirectory()
        self.local_root = Path(self._tmp.name)

    def tearDown(self) -> None:
        try:
            _remove_tree(self.session._sftp, self.remote_root)
        except OSError:
            pass
        self.session.close()
        self._tmp.cleanup()

    def test_list_dir_shows_uploaded_entries(self) -> None:
        (self.local_root / "hello.txt").write_bytes(b"hello")
        self.session.upload(self.local_root / "hello.txt", posixpath.join(self.remote_root, "hello.txt"))

        names = [e.name for e in self.session.list_dir(self.remote_root)]

        self.assertEqual(names, ["hello.txt"])

    def test_upload_and_download_tree_is_byte_identical(self) -> None:
        src = self.local_root / "src"
        (src / "nested" / "deeper").mkdir(parents=True)
        payloads = {
            "text.txt": b"line1\r\nline2\n",
            "nested/bin.dat": bytes(range(256)) * 64,
            "nested/deeper/empty.txt": b"",
        }
        for rel, data in payloads.items():
            (src / rel).write_bytes(data)

        self.session.upload_paths([src], self.remote_root)

        dest = self.local_root / "dest"
        dest.mkdir()
        top = [e for e in self.session.list_dir(self.remote_root) if e.name == "src"]
        self.session.download_entries(top, dest)

        for rel, data in payloads.items():
            self.assertEqual((dest / "src" / rel).read_bytes(), data, rel)

    def test_progress_reported_for_each_top_level_item(self) -> None:
        for name in ("a.txt", "b.txt"):
            (self.local_root / name).write_bytes(name.encode())
        progress: list[tuple[int, int]] = []

        self.session.upload_paths(
            [self.local_root / "a.txt", self.local_root / "b.txt"],
            self.remote_root,
            lambda done, total: progress.append((done, total)),
        )

        self.assertEqual(progress, [(1, 2), (2, 2)])

    def test_reupload_into_existing_directory_succeeds(self) -> None:
        src = self.local_root / "again"
        src.mkdir()
        (src / "x.txt").write_bytes(b"1")
        self.session.upload_paths([src], self.remote_root)
        (src / "x.txt").write_bytes(b"22")

        self.session.upload_paths([src], self.remote_root)

        dest = self.local_root / "out"
        dest.mkdir()
        top = [e for e in self.session.list_dir(self.remote_root) if e.name == "again"]
        self.session.download_entries(top, dest)
        self.assertEqual((dest / "again" / "x.txt").read_bytes(), b"22")

    def test_download_missing_remote_file_raises_remote_error(self) -> None:
        with self.assertRaises(RemoteError):
            self.session.download(
                posixpath.join(self.remote_root, "missing.txt"),
                self.local_root / "missing.txt",
            )

    def test_list_missing_directory_raises_remote_error(self) -> None:
        with self.assertRaises(RemoteError):
            self.session.list_dir(posixpath.join(self.remote_root, "nope"))

    def test_upload_over_file_name_of_directory_is_rejected(self) -> None:
        (self.local_root / "clash.txt").write_bytes(b"f")
        self.session.upload(self.local_root / "clash.txt", posixpath.join(self.remote_root, "clash"))
        folder = self.local_root / "clash"
        folder.mkdir()
        (folder / "in.txt").write_bytes(b"x")

        with self.assertRaises(RemoteError):
            self.session.upload_paths([folder], self.remote_root)

    def test_wrong_password_is_rejected(self) -> None:
        with self.assertRaises(RemoteError):
            RemoteSession(self.profile, "definitely-wrong-password")


if __name__ == "__main__":
    unittest.main()
