"""Git サービス - subprocess で git CLI を呼び出す。

依存パッケージは不要。git が PATH にある場合のみ機能する。
GIT_TERMINAL_PROMPT=0 で認証プロンプト待ちによるフリーズを防ぐ。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


class GitError(Exception):
    """git コマンド失敗時のエラー。"""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


def _run(
    repo: str | Path,
    *args: str,
    timeout: int = 30,
    allow_exit1: bool = False,
) -> subprocess.CompletedProcess:
    """git コマンドを実行して CompletedProcess を返す。"""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        creationflags=flags,
    )
    if result.returncode != 0 and not (allow_exit1 and result.returncode == 1):
        raise GitError(
            f"git {' '.join(args)} failed (exit {result.returncode}): "
            f"{result.stderr.strip()}",
            stderr=result.stderr.strip(),
        )
    return result


def is_available() -> bool:
    """git が PATH にあるか確認する。"""
    return shutil.which("git") is not None


def find_repo_root(path: str | Path) -> Path | None:
    """path 以上にある git リポジトリのルートを返す。見つからなければ None。"""
    if not is_available():
        return None
    try:
        result = _run(path, "rev-parse", "--show-toplevel")
        root = result.stdout.strip()
        if root:
            return Path(root)
    except (GitError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def current_branch(repo: str | Path) -> str:
    """現在のブランチ名を返す。detached HEAD の場合は短縮ハッシュ。"""
    try:
        result = _run(repo, "branch", "--show-current")
        branch = result.stdout.strip()
        if branch:
            return branch
        # detached HEAD の場合
        result = _run(repo, "rev-parse", "--short", "HEAD")
        return result.stdout.strip() or "HEAD"
    except (GitError, subprocess.TimeoutExpired):
        return "HEAD"


def status(repo: str | Path) -> dict[Path, str]:
    """リポジトリの Git 状態を {絶対パス: 2文字コード} として返す。

    コードは git status --porcelain=v1 の XY 形式（例: ' M', 'A ', '??'）。
    ディレクトリには配下に変更があれば 'D~' を設定する。
    """
    result = _run(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    output = result.stdout

    status_map: dict[Path, str] = {}
    repo_path = Path(str(repo)).resolve()

    # porcelain v1 -z: NUL 区切り。リネームは "XY new_path\0old_path\0" の2部構成
    tokens = output.split("\0")
    i = 0
    while i < len(tokens):
        entry = tokens[i]
        if len(entry) < 4:
            i += 1
            continue

        xy = entry[:2]
        file_path = entry[3:]

        abs_path = (repo_path / file_path).resolve()
        status_map[abs_path] = xy

        # リネーム・コピーは次のトークンが旧パスなのでスキップ
        if xy[0] in "RC" and i + 1 < len(tokens):
            i += 2
        else:
            i += 1

    # 変更があるファイルの親ディレクトリを「配下に変更あり」としてマーク
    dirty_dirs: set[Path] = set()
    for abs_path in list(status_map.keys()):
        try:
            rel = abs_path.relative_to(repo_path)
            path_parts = rel.parts
            for j in range(1, len(path_parts)):
                parent = repo_path.joinpath(*path_parts[:j])
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
    _run(repo, "add", "--", *[str(p) for p in paths])


def unstage(repo: str | Path, paths: list[Path]) -> None:
    """ステージを解除する（git restore --staged）。"""
    if not paths:
        return
    _run(repo, "restore", "--staged", "--", *[str(p) for p in paths])


def commit(repo: str | Path, message: str) -> str:
    """コミットして git の出力を返す。"""
    result = _run(repo, "commit", "-m", message)
    return result.stdout.strip()


def pull(repo: str | Path) -> str:
    """git pull を実行して stdout + stderr を返す。"""
    result = _run(repo, "pull", timeout=120)
    return result.stdout + result.stderr


def push(repo: str | Path) -> str:
    """git push を実行して stdout + stderr を返す。"""
    result = _run(repo, "push", timeout=120)
    return result.stdout + result.stderr


def diff(repo: str | Path, path: Path) -> str:
    """ファイルの git diff HEAD 結果を返す。未追跡ファイルも処理する。"""
    try:
        result = _run(repo, "diff", "HEAD", "--", str(path))
        if result.stdout:
            return result.stdout
    except GitError:
        pass

    # 未追跡ファイル: --no-index で diff（終了コード 1 は正常）
    try:
        result = _run(
            repo, "diff", "--no-index", "--",
            os.devnull, str(path),
            allow_exit1=True,
        )
        return result.stdout
    except (GitError, subprocess.TimeoutExpired):
        return ""


def log(
    repo: str | Path, limit: int = 200
) -> list[tuple[str, str, str, str]]:
    """ログエントリ一覧 [(hash, date, author, subject), ...] を返す。"""
    sep = "\x1f"
    fmt = f"%h{sep}%ad{sep}%an{sep}%s"
    result = _run(
        repo, "log",
        f"--pretty=format:{fmt}",
        "--date=format:%Y-%m-%d %H:%M",
        f"-{limit}",
    )
    entries: list[tuple[str, str, str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split(sep, 3)
        if len(parts) == 4:
            entries.append((parts[0], parts[1], parts[2], parts[3]))
    return entries


def show(repo: str | Path, commit_hash: str) -> str:
    """コミットの詳細差分を返す（git show）。"""
    result = _run(repo, "show", commit_hash)
    return result.stdout


def branches(repo: str | Path) -> list[str]:
    """ローカルブランチ一覧を返す。現在のブランチは先頭に置く。"""
    result = _run(repo, "branch")
    branch_list: list[str] = []
    current: str | None = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("* "):
            current = line[2:].strip()
        elif line:
            branch_list.append(line)
    if current:
        return [current] + branch_list
    return branch_list


def switch(repo: str | Path, branch: str) -> None:
    """ブランチを切り替える（git switch）。"""
    _run(repo, "switch", branch)
