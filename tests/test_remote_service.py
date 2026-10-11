from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import paramiko

from ore_filer.services import remote_service
from ore_filer.services.remote_service import RemoteError, RemoteProfile, RemoteSession


def _attr(name: str, *, is_dir: bool, size: int = 0, mtime: int = 0):
    attr = mock.Mock()
    attr.filename = name
    attr.st_mode = (stat.S_IFDIR if is_dir else stat.S_IFREG) | 0o644
    attr.st_size = size
    attr.st_mtime = mtime
    return attr


def _session(sftp: mock.Mock) -> RemoteSession:
    profile = RemoteProfile(name="test", host="example.com", username="user")
    with mock.patch.object(remote_service.paramiko, "SSHClient") as client_cls:
        client = client_cls.return_value
        client.open_sftp.return_value = sftp
        return RemoteSession(profile, password="secret")


class RemoteSessionTest(unittest.TestCase):
    def test_list_dir_puts_directories_first_and_skips_dot_entries(self) -> None:
        sftp = mock.Mock()
        sftp.listdir_attr.return_value = [
            _attr("b.txt", is_dir=False, size=10),
            _attr(".", is_dir=True),
            _attr("Zeta", is_dir=True),
            _attr("..", is_dir=True),
            _attr("alpha", is_dir=True),
        ]
        session = _session(sftp)

        entries = session.list_dir("/home/user")

        self.assertEqual([e.name for e in entries], ["alpha", "Zeta", "b.txt"])
        self.assertEqual(entries[0].path, "/home/user/alpha")
        self.assertTrue(entries[0].is_dir)
        self.assertFalse(entries[2].is_dir)
        self.assertEqual(entries[2].size, 10)

    def test_list_dir_wraps_os_error(self) -> None:
        sftp = mock.Mock()
        sftp.listdir_attr.side_effect = OSError("denied")
        session = _session(sftp)

        with self.assertRaises(RemoteError):
            session.list_dir("/root")

    def test_connect_failure_is_reported_as_remote_error(self) -> None:
        profile = RemoteProfile(name="t", host="h", username="u")
        with mock.patch.object(remote_service.paramiko, "SSHClient") as client_cls:
            client_cls.return_value.connect.side_effect = paramiko.AuthenticationException("bad")
            with self.assertRaises(RemoteError):
                RemoteSession(profile, password="x")

    def test_parent_dir(self) -> None:
        session = _session(mock.Mock())
        self.assertEqual(session.parent_dir("/home/user/docs"), "/home/user")
        self.assertEqual(session.parent_dir("/home/user/docs/"), "/home/user")
        self.assertEqual(session.parent_dir("/"), "/")


def _dir_attr(name: str) -> mock.Mock:
    return _attr(name, is_dir=True)


def _file_attr(name: str, size: int = 0) -> mock.Mock:
    return _attr(name, is_dir=False, size=size)


class RemoteTransferTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _remote_tree(self) -> mock.Mock:
        tree = {
            "/srv/d": [_file_attr("a.txt", 3), _dir_attr("sub")],
            "/srv/d/sub": [_file_attr("b.txt", 4)],
        }
        sftp = mock.Mock()
        sftp.listdir_attr.side_effect = lambda path: tree[path]
        sftp.get.side_effect = lambda remote, local: Path(local).write_bytes(
            remote.encode("utf-8")
        )
        return sftp

    def test_download_entries_recreates_tree_and_reports_progress(self) -> None:
        sftp = self._remote_tree()
        session = _session(sftp)
        entries = [
            remote_service.RemoteEntry("d", "/srv/d", True, 0, 0.0),
            remote_service.RemoteEntry("f.txt", "/srv/f.txt", False, 1, 0.0),
        ]
        progress: list[tuple[int, int]] = []

        session.download_entries(entries, self.root, lambda d, t: progress.append((d, t)))

        self.assertEqual((self.root / "d" / "a.txt").read_bytes(), b"/srv/d/a.txt")
        self.assertEqual((self.root / "d" / "sub" / "b.txt").read_bytes(), b"/srv/d/sub/b.txt")
        self.assertEqual(progress, [(1, 2), (2, 2)])

    def test_download_entries_without_progress_callback(self) -> None:
        sftp = self._remote_tree()
        session = _session(sftp)
        entries = [remote_service.RemoteEntry("d", "/srv/d", True, 0, 0.0)]

        session.download_entries(entries, self.root)

        self.assertTrue((self.root / "d" / "sub" / "b.txt").is_file())

    def test_download_wraps_os_error(self) -> None:
        sftp = mock.Mock()
        sftp.get.side_effect = OSError("no such file")
        session = _session(sftp)

        with self.assertRaises(RemoteError):
            session.download("/srv/missing", self.root / "missing")

    def test_upload_paths_recurses_and_reports_progress(self) -> None:
        local = self.root / "src"
        (local / "sub").mkdir(parents=True)
        (local / "a.txt").write_text("a", encoding="utf-8")
        (local / "sub" / "b.txt").write_text("b", encoding="utf-8")
        sftp = mock.Mock()
        session = _session(sftp)
        progress: list[tuple[int, int]] = []

        session.upload_paths([local], "/srv/remote", lambda d, t: progress.append((d, t)))

        mkdir_paths = [c.args[0] for c in sftp.mkdir.call_args_list]
        put_pairs = [(c.args[0], c.args[1]) for c in sftp.put.call_args_list]
        self.assertEqual(mkdir_paths, ["/srv/remote/src", "/srv/remote/src/sub"])
        self.assertIn((str(local / "a.txt"), "/srv/remote/src/a.txt"), put_pairs)
        self.assertIn((str(local / "sub" / "b.txt"), "/srv/remote/src/sub/b.txt"), put_pairs)
        self.assertEqual(progress, [(1, 1)])

    def test_upload_wraps_os_error(self) -> None:
        local = self.root / "x.txt"
        local.write_text("x", encoding="utf-8")
        sftp = mock.Mock()
        sftp.put.side_effect = OSError("denied")
        session = _session(sftp)

        with self.assertRaises(RemoteError):
            session.upload(local, "/srv/x.txt")

    def test_ensure_remote_dir_accepts_existing_directory(self) -> None:
        sftp = mock.Mock()
        sftp.mkdir.side_effect = OSError("exists")
        sftp.stat.return_value = mock.Mock(st_mode=stat.S_IFDIR | 0o755)
        session = _session(sftp)

        session._ensure_remote_dir("/srv/existing")

    def test_ensure_remote_dir_rejects_file_with_same_name(self) -> None:
        sftp = mock.Mock()
        sftp.mkdir.side_effect = OSError("exists")
        sftp.stat.return_value = mock.Mock(st_mode=stat.S_IFREG | 0o644)
        session = _session(sftp)

        with self.assertRaises(RemoteError):
            session._ensure_remote_dir("/srv/name")

    def test_ensure_remote_dir_wraps_stat_failure(self) -> None:
        sftp = mock.Mock()
        sftp.mkdir.side_effect = OSError("exists")
        sftp.stat.side_effect = OSError("gone")
        session = _session(sftp)

        with self.assertRaises(RemoteError):
            session._ensure_remote_dir("/srv/name")


if __name__ == "__main__":
    unittest.main()
