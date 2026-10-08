"""Git サービス - Dulwich で Git リポジトリを操作する。"""
from __future__ import annotations

from datetime import datetime
from difflib import unified_diff
from io import BytesIO
import os
from pathlib import Path

try:
    from dulwich import porcelain
    from dulwich.index import IndexEntry
    from dulwich.repo import Repo
except ImportError:  # pragma: no cover - pyproject.tomlで常に導入する
    porcelain = None
    IndexEntry = None
    Repo = None


class GitError(Exception):
    """Git 操作失敗時のエラー。"""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


class _CaptureStream:
    """Dulwichのbytes/text両方の出力ストリームを受ける。"""

    def __init__(self) -> None:
        self._parts: list[str] = []

    def write(self, value: str | bytes) -> int:
        if isinstance(value, bytes):
            text = value.decode("utf-8", errors="replace")
        else:
            text = value
        self._parts.append(text)
        return len(value)

    def flush(self) -> None:
        return

    def text(self) -> str:
        return "".join(self._parts)


def _require_dulwich():
    if porcelain is None or Repo is None:
        raise GitError("Dulwichがインストールされていません。")
    return porcelain, Repo


def _open_repo(repo: str | Path):
    _, repo_type = _require_dulwich()
    return repo_type(str(Path(repo).expanduser().resolve()))


def _repo_root(repo) -> Path:
    return Path(str(repo.path)).expanduser().resolve()


def _decode_path(value: str | bytes) -> str:
    return os.fsdecode(value) if isinstance(value, bytes) else value


def _object_id_text(value: str | bytes) -> str:
    if isinstance(value, bytes):
        return value.decode("ascii", errors="replace")
    return value


def _person_name(value: str | bytes) -> str:
    text = _decode_path(value).strip()
    return text.rsplit(" <", 1)[0] if " <" in text else text


def _relative_paths(repo, paths: list[Path]) -> list[str]:
    root = _repo_root(repo)
    result: list[str] = []
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        try:
            relative = resolved.relative_to(root)
        except ValueError as error:
            raise GitError(f"リポジトリ外のパスです: {path}") from error
        result.append(relative.as_posix() or ".")
    return result


def _set_status(
    status_map: dict[Path, str],
    path: Path,
    *,
    index_code: str | None = None,
    worktree_code: str | None = None,
) -> None:
    xy = list(status_map.get(path, "  "))
    if index_code is not None:
        xy[0] = index_code
    if worktree_code is not None:
        xy[1] = worktree_code
    status_map[path] = "".join(xy)


def is_available() -> bool:
    """Dulwichが利用可能か確認する。"""
    return porcelain is not None and Repo is not None


def find_repo_root(path: str | Path) -> Path | None:
    """path 以上にある git リポジトリのルートを返す。見つからなければ None。"""
    if not is_available():
        return None
    try:
        _, repo_type = _require_dulwich()
        start = Path(path).expanduser().resolve()
        if start.is_file():
            start = start.parent
        return Path(str(repo_type.discover(str(start)).path)).resolve()
    except Exception:
        pass
    return None


def current_branch(repo: str | Path) -> str:
    """現在のブランチ名を返す。detached HEAD の場合は短縮ハッシュ。"""
    try:
        repo_obj = _open_repo(repo)
        refs, head = repo_obj.refs.follow(b"HEAD")
        for ref in reversed(refs):
            if ref.startswith(b"refs/heads/"):
                return _decode_path(ref[len(b"refs/heads/"):])
        return _object_id_text(head)[:7] or "HEAD"
    except Exception:
        return "HEAD"


def status(repo: str | Path) -> dict[Path, str]:
    """リポジトリの Git 状態を {絶対パス: 2文字コード} として返す。

    コードは git status --porcelain=v1 の XY 形式（例: ' M', 'A ', '??'）。
    ディレクトリには配下に変更があれば 'D~' を設定する。
    """
    repo_obj = _open_repo(repo)
    root = _repo_root(repo_obj)
    result = porcelain.status(repo_obj, untracked_files="all")
    status_map: dict[Path, str] = {}

    staged_codes = {"add": "A", "delete": "D", "modify": "M"}
    for category, code in staged_codes.items():
        for raw_path in result.staged.get(category, []):
            path = (root / Path(_decode_path(raw_path))).resolve()
            _set_status(status_map, path, index_code=code)

    for raw_path in result.unstaged:
        path = (root / Path(_decode_path(raw_path))).resolve()
        _set_status(
            status_map,
            path,
            worktree_code="D" if not path.exists() else "M",
        )

    for raw_path in result.untracked:
        path = (root / Path(_decode_path(raw_path))).resolve()
        _set_status(status_map, path, index_code="?", worktree_code="?")

    # 変更があるファイルの親ディレクトリを「配下に変更あり」としてマーク
    dirty_dirs: set[Path] = set()
    for abs_path in list(status_map.keys()):
        try:
            rel = abs_path.relative_to(root)
            path_parts = rel.parts
            for j in range(1, len(path_parts)):
                parent = root.joinpath(*path_parts[:j])
                dirty_dirs.add(parent)
        except ValueError:
            pass

    for d in dirty_dirs:
        if d not in status_map:
            status_map[d] = "D~"

    return status_map


def stage(repo: str | Path, paths: list[Path]) -> None:
    """ファイルをステージする（git add）。"""
    if not paths:
        return
    repo_obj = _open_repo(repo)
    porcelain.add(repo_obj, paths=_relative_paths(repo_obj, paths))


def unstage(repo: str | Path, paths: list[Path]) -> None:
    """ステージを解除する（git restore --staged）。"""
    if not paths:
        return
    repo_obj = _open_repo(repo)
    root = _repo_root(repo_obj)
    tracked_status = status(repo_obj.path)
    index = repo_obj.open_index()
    try:
        head = repo_obj.head()
    except KeyError:
        for path in paths:
            resolved = Path(path).expanduser().resolve()
            for candidate, code in tracked_status.items():
                if code != "D~" and (
                    candidate == resolved or resolved in candidate.parents
                ):
                    relative = candidate.relative_to(root).as_posix()
                    relative_bytes = os.fsencode(relative)
                    try:
                        del index[relative_bytes]
                    except KeyError:
                        pass
        index.write()
        return
    head_commit = repo_obj[head]
    head_tree = repo_obj[head_commit.tree]

    candidates: set[Path] = set()
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        candidates.update(
            candidate
            for candidate, code in tracked_status.items()
            if code != "D~"
            and (candidate == resolved or resolved in candidate.parents)
        )
        if not any(
            candidate == resolved or resolved in candidate.parents
            for candidate in tracked_status
        ):
            candidates.add(resolved)

    for candidate in candidates:
        relative = candidate.relative_to(root).as_posix()
        relative_bytes = os.fsencode(relative)
        try:
            mode, sha = head_tree.lookup_path(repo_obj.__getitem__, relative_bytes)
        except KeyError:
            if relative_bytes in index:
                del index[relative_bytes]
            continue

        try:
            current = index[relative_bytes]
        except KeyError:
            current = None
        if current is None:
            index[relative_bytes] = IndexEntry(
                0, 0, 0, 0, mode, 0, 0, 0, sha
            )
        else:
            index[relative_bytes] = IndexEntry(
                0,
                0,
                0,
                0,
                mode,
                0,
                0,
                0,
                sha,
                current.flags,
                current.extended_flags,
            )
    index.write()


def commit(repo: str | Path, message: str) -> str:
    """コミットして git の出力を返す。"""
    repo_obj = _open_repo(repo)
    commit_id = porcelain.commit(repo_obj, message=message.encode("utf-8"))
    return f"commit {_object_id_text(commit_id)[:7]}"


def _remote_call(func, repo, operation: str, **kwargs):
    output = _CaptureStream()
    try:
        result = func(repo, outstream=output, errstream=output, **kwargs)
    except Exception as error:
        detail = output.text().strip() or str(error)
        raise GitError(f"{operation} failed: {detail}", stderr=detail) from error
    return result, output.text().strip()


def _ssh_transport_kwargs(
    ssh_command: str | None = None,
    key_filename: str | Path | None = None,
) -> dict[str, str]:
    kwargs: dict[str, str] = {}
    if ssh_command:
        kwargs["ssh_command"] = ssh_command
    if key_filename:
        kwargs["key_filename"] = str(Path(key_filename).expanduser())
    return kwargs


def fetch(
    repo: str | Path,
    remote_location: str | Path | None = None,
    *,
    ssh_command: str | None = None,
    key_filename: str | Path | None = None,
) -> str:
    """リモートの参照を取得する（作業ツリーは変更しない）。"""
    repo_obj = _open_repo(repo)
    try:
        kwargs = _ssh_transport_kwargs(ssh_command, key_filename)
        if remote_location is not None:
            kwargs["remote_location"] = str(remote_location)
        result, output = _remote_call(
            porcelain.fetch,
            repo_obj,
            "fetch",
            **kwargs,
        )
        return output or f"fetch completed ({len(result.refs)} refs)"
    finally:
        repo_obj.close()


def pull(
    repo: str | Path,
    *,
    ssh_command: str | None = None,
    key_filename: str | Path | None = None,
) -> str:
    """リモートからfast-forwardのみで取り込む。"""
    repo_obj = _open_repo(repo)
    try:
        local_status = porcelain.status(repo_obj, untracked_files="all")
        if (
            any(local_status.staged.values())
            or local_status.unstaged
            or local_status.untracked
        ):
            raise GitError("pull failed: ローカルに未コミットの変更があります")
        result, output = _remote_call(
            porcelain.pull,
            repo_obj,
            "pull",
            fast_forward=True,
            ff_only=True,
            force=True,
            **_ssh_transport_kwargs(ssh_command, key_filename),
        )
        return output or "pull completed"
    finally:
        repo_obj.close()


def push(
    repo: str | Path,
    *,
    ssh_command: str | None = None,
    key_filename: str | Path | None = None,
) -> str:
    """SSH鍵またはssh-agentを使って現在のブランチをリモートへ送信する。"""
    repo_obj = _open_repo(repo)
    try:
        result, output = _remote_call(
            porcelain.push,
            repo_obj,
            "push",
            **_ssh_transport_kwargs(ssh_command, key_filename),
        )
        return output or "push completed"
    finally:
        repo_obj.close()


def diff(repo: str | Path, path: Path) -> str:
    """ファイルの git diff HEAD 結果を返す。未追跡ファイルも処理する。"""
    repo_obj = _open_repo(repo)
    resolved = Path(path).expanduser().resolve()
    relative = _relative_paths(repo_obj, [resolved])[0]
    output = BytesIO()
    try:
        porcelain.diff(
            repo_obj,
            commit="HEAD",
            paths=[os.fsencode(relative)],
            outstream=output,
        )
    except Exception as error:
        raise GitError(f"diff failed: {error}") from error
    text = output.getvalue().decode("utf-8", errors="replace")
    if text:
        return text

    if not resolved.is_file():
        return ""
    raw_status = porcelain.status(repo_obj, untracked_files="all")
    if os.fsencode(relative) not in raw_status.untracked:
        return ""
    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "".join(
        unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile=os.devnull,
            tofile=str(resolved),
        )
    )


def log(
    repo: str | Path, limit: int = 200
) -> list[tuple[str, str, str, str]]:
    """ログエントリ一覧 [(hash, date, author, subject), ...] を返す。"""
    repo_obj = _open_repo(repo)
    entries: list[tuple[str, str, str, str]] = []
    for entry in repo_obj.get_walker(max_entries=limit):
        commit = entry.commit
        message = _decode_path(commit.message)
        subject = message.splitlines()[0] if message.splitlines() else ""
        date = datetime.fromtimestamp(commit.commit_time).strftime("%Y-%m-%d %H:%M")
        entries.append(
            (
                _object_id_text(commit.id)[:7],
                date,
                _person_name(commit.author),
                subject,
            )
        )
    return entries


def _resolve_commit(repo, commit_hash: str):
    needle = commit_hash.strip().lower()
    for entry in repo.get_walker():
        commit = entry.commit
        if _object_id_text(commit.id).lower().startswith(needle):
            return commit
    raise GitError(f"コミットが見つかりません: {commit_hash}")


def show(repo: str | Path, commit_hash: str) -> str:
    """コミットの詳細差分を返す（git show）。"""
    repo_obj = _open_repo(repo)
    commit = _resolve_commit(repo_obj, commit_hash)
    output = BytesIO()
    try:
        if commit.parents:
            porcelain.diff(
                repo_obj,
                commit=commit.parents[0],
                commit2=commit.id,
                outstream=output,
            )
        else:
            porcelain.diff(repo_obj, commit2=commit.id, outstream=output)
    except Exception as error:
        raise GitError(f"show failed: {error}") from error

    message = _decode_path(commit.message)
    subject = message.splitlines()[0] if message.splitlines() else ""
    header = (
        f"commit {_object_id_text(commit.id)}\n"
        f"Author: {_person_name(commit.author)}\n"
        f"Date: {datetime.fromtimestamp(commit.commit_time):%Y-%m-%d %H:%M}\n\n"
        f"    {subject}\n\n"
    )
    return header + output.getvalue().decode("utf-8", errors="replace")


def branches(repo: str | Path) -> list[str]:
    """ローカルブランチ一覧を返す。現在のブランチは先頭に置く。"""
    repo_obj = _open_repo(repo)
    prefix = b"refs/heads/"
    branch_list = [
        _decode_path(ref[len(prefix):])
        for ref in repo_obj.refs.as_dict()
        if ref.startswith(prefix)
    ]
    branch_list.sort(key=str.casefold)
    current = current_branch(repo_obj.path)
    if current:
        return [current] + [branch for branch in branch_list if branch != current]
    return branch_list


def switch(repo: str | Path, branch: str) -> None:
    """ブランチを切り替える（git switch）。"""
    repo_obj = _open_repo(repo)
    try:
        porcelain.checkout(repo_obj, target=branch)
    except Exception as error:
        raise GitError(f"ブランチ切替に失敗しました: {error}") from error
