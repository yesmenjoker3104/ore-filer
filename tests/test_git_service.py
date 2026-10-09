import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dulwich import porcelain
from dulwich.repo import Repo

from ore_filer.services import git_service


def _init_repo(path: Path) -> None:
    repo = porcelain.init(path)
    config = repo.get_config()
    config.set((b"user",), b"name", b"Test User")
    config.set((b"user",), b"email", b"test@example.com")
    config.write_to_path()
    repo.close()


def _configure_origin(path: Path, remote: Path) -> None:
    repo = Repo(path)
    try:
        branch_ref = next(
            ref for ref in repo.refs.as_dict()
            if ref.startswith(b"refs/heads/")
        )
        branch = branch_ref.removeprefix(b"refs/heads/")
        config = repo.get_config()
        config.set((b"remote", b"origin"), b"url", str(remote).encode())
        config.set(
            (b"remote", b"origin"),
            b"fetch",
            b"+refs/heads/*:refs/remotes/origin/*",
        )
        config.set((b"branch", branch), b"remote", b"origin")
        config.set((b"branch", branch), b"merge", branch_ref)
        config.write_to_path()
    finally:
        repo.close()


def _head(path: Path) -> bytes:
    repo = Repo(path)
    try:
        return repo.head()
    finally:
        repo.close()


def _refs(path: Path) -> dict[bytes, bytes]:
    repo = Repo(path)
    try:
        return repo.refs.as_dict()
    finally:
        repo.close()


class GitServiceTests(unittest.TestCase):
    def test_local_operations(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("one\n", encoding="utf-8")

            self.assertEqual(git_service.status(root)[path.resolve()], "??")
            git_service.stage(root, [path])
            self.assertEqual(git_service.status(root)[path.resolve()], "A ")
            git_service.commit(root, "initial")
            self.assertEqual(git_service.status(root), {})
            initial_entry = git_service.log(root, limit=1)[0]
            self.assertIn("+one", git_service.show(root, initial_entry[0]))

            path.write_text("two\n", encoding="utf-8")
            self.assertEqual(git_service.status(root)[path.resolve()], " M")
            self.assertIn("-one", git_service.diff(root, path))
            self.assertIn("+two", git_service.diff(root, path))
            git_service.stage(root, [path])
            self.assertEqual(git_service.status(root)[path.resolve()], "M ")
            git_service.unstage(root, [path])
            self.assertEqual(git_service.status(root)[path.resolve()], " M")
            self.assertEqual(path.read_text(encoding="utf-8"), "two\n")

            git_service.stage(root, [path])
            git_service.commit(root, "change")
            porcelain.branch_create(root, "feature")
            git_service.switch(root, "feature")
            self.assertEqual(git_service.current_branch(root), "feature")

            diff = git_service.diff(root, path)
            self.assertEqual(diff, "")
            entry = git_service.log(root, limit=1)[0]
            self.assertEqual(entry[3], "change")
            self.assertIn("change", git_service.show(root, entry[0]))

    def test_fetch_and_push_with_bare_remote(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            source = base / "source"
            remote = base / "remote.git"
            clone = base / "clone"
            _init_repo(source)
            path = source / "note.txt"
            path.write_text("one\n", encoding="utf-8")
            git_service.stage(source, [path])
            git_service.commit(source, "initial")

            remote_repo = porcelain.init(remote, bare=True)
            remote_repo.close()
            _configure_origin(source, remote)
            self.assertTrue(git_service.push(source))

            clone_repo = porcelain.clone(
                str(remote),
                str(clone),
                errstream=io.BytesIO(),
            )
            clone_repo.close()

            path.write_text("two\n", encoding="utf-8")
            git_service.stage(source, [path])
            git_service.commit(source, "change")
            self.assertTrue(git_service.push(source))
            self.assertTrue(git_service.fetch(clone))

            refs = _refs(clone)
            self.assertEqual(refs[b"refs/remotes/origin/master"], _head(source))

    def test_push_passes_ssh_transport_options(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            with patch.object(
                git_service.porcelain,
                "push",
                return_value=object(),
            ) as push_mock:
                self.assertEqual(
                    git_service.push(
                        root,
                        ssh_command="ssh -o BatchMode=yes",
                        key_filename=root / "id_ed25519",
                    ),
                    "push completed",
                )

            options = push_mock.call_args.kwargs
            self.assertEqual(options["ssh_command"], "ssh -o BatchMode=yes")
            self.assertEqual(
                options["key_filename"],
                str(root / "id_ed25519"),
            )

    def test_pull_fast_forwards_worktree(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            source = base / "source"
            remote = base / "remote.git"
            clone = base / "clone"
            _init_repo(source)
            path = source / "note.txt"
            path.write_text("one\n", encoding="utf-8")
            git_service.stage(source, [path])
            git_service.commit(source, "initial")

            remote_repo = porcelain.init(remote, bare=True)
            remote_repo.close()
            _configure_origin(source, remote)
            git_service.push(source)
            clone_repo = porcelain.clone(
                str(remote),
                str(clone),
                errstream=io.BytesIO(),
            )
            clone_repo.close()

            path.write_text("two\n", encoding="utf-8")
            git_service.stage(source, [path])
            git_service.commit(source, "change")
            git_service.push(source)

            self.assertTrue(git_service.pull(clone))
            self.assertEqual(
                (clone / "note.txt").read_text(encoding="utf-8"),
                "two\n",
            )
            self.assertEqual(_head(clone), _head(source))

    def test_unstage_before_first_commit_keeps_untracked_file(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("one\n", encoding="utf-8")
            git_service.stage(root, [path])
            git_service.unstage(root, [path])

            self.assertEqual(git_service.status(root)[path.resolve()], "??")
            self.assertEqual(path.read_text(encoding="utf-8"), "one\n")

    def test_deleted_file_status_can_be_staged_and_unstaged(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("one\n", encoding="utf-8")
            git_service.stage(root, [path])
            git_service.commit(root, "initial")
            path.unlink()

            self.assertEqual(git_service.status(root)[path.resolve()], " D")
            git_service.stage(root, [path])
            self.assertEqual(git_service.status(root)[path.resolve()], "D ")
            git_service.unstage(root, [path])
            self.assertEqual(git_service.status(root)[path.resolve()], " D")

    def test_head_file_text_returns_committed_content(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_bytes(b"hello\n")
            git_service.stage(root, [path])
            git_service.commit(root, "initial")

            # コミット後の内容が取得できる
            self.assertEqual(git_service.head_file_text(root, path), "hello\n")

            # ワーキングツリーを変更してもHEADは変わらない
            path.write_bytes(b"world\n")
            self.assertEqual(git_service.head_file_text(root, path), "hello\n")

    def test_head_file_text_returns_empty_for_untracked(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            path = root / "new.txt"
            path.write_text("untracked\n", encoding="utf-8")

            # 未追跡ファイルは空文字
            self.assertEqual(git_service.head_file_text(root, path), "")

    def test_get_remote_url_returns_configured_url(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("x\n", encoding="utf-8")
            git_service.stage(root, [path])
            git_service.commit(root, "initial")

            remote_url = "https://example.com/repo.git"
            repo = __import__("dulwich.repo", fromlist=["Repo"]).Repo(str(root))
            config = repo.get_config()
            config.set((b"remote", b"origin"), b"url", remote_url.encode())
            config.write_to_path()
            repo.close()

            self.assertEqual(git_service.get_remote_url(root), remote_url)
            self.assertTrue(git_service.is_http_remote(root))

    def test_is_http_remote_false_for_ssh(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_text("x\n", encoding="utf-8")
            git_service.stage(root, [path])
            git_service.commit(root, "initial")

            repo = __import__("dulwich.repo", fromlist=["Repo"]).Repo(str(root))
            config = repo.get_config()
            config.set((b"remote", b"origin"), b"url", b"git@github.com:user/repo.git")
            config.write_to_path()
            repo.close()

            self.assertFalse(git_service.is_http_remote(root))

    def test_push_embeds_http_credentials_in_url(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            # HTTPSリモートを設定
            repo_obj = __import__("dulwich.repo", fromlist=["Repo"]).Repo(str(root))
            config = repo_obj.get_config()
            config.set((b"remote", b"origin"), b"url", b"https://github.com/user/repo.git")
            config.write_to_path()
            repo_obj.close()

            with patch.object(
                git_service.porcelain,
                "push",
                return_value=object(),
            ) as push_mock:
                git_service.push(root, username="user", password="token")

            loc = push_mock.call_args.kwargs.get("remote_location", "")
            self.assertIn("user", loc)
            self.assertIn("token", loc)
            self.assertIn("github.com", loc)

    def test_pull_embeds_http_credentials_in_url(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "repo"
            _init_repo(root)
            path = root / "note.txt"
            path.write_bytes(b"x\n")
            git_service.stage(root, [path])
            git_service.commit(root, "initial")
            repo_obj = __import__("dulwich.repo", fromlist=["Repo"]).Repo(str(root))
            config = repo_obj.get_config()
            config.set((b"remote", b"origin"), b"url", b"https://github.com/user/repo.git")
            config.write_to_path()
            repo_obj.close()

            with patch.object(
                git_service.porcelain,
                "pull",
                return_value=object(),
            ) as pull_mock:
                try:
                    git_service.pull(root, username="user", password="token")
                except Exception:
                    pass

            loc = pull_mock.call_args.kwargs.get("remote_location", "")
            self.assertIn("user", loc)
            self.assertIn("token", loc)


if __name__ == "__main__":
    unittest.main()
