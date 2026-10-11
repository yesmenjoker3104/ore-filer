from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ore_filer.services import git_service
from ore_filer.services.file_operations import (
    ArchiveEntry,
    copy_archive_entries,
    create_archive,
    extract_archive,
)
from ore_filer.services.remote_service import RemoteProfile, RemoteSession
from ore_filer.services.search_service import find_paths, grep_files


class FileOperationThread(QThread):
	completed = Signal()
	failed = Signal(str)
	progress = Signal(int, int)

	def __init__(self, func: Callable, parent=None):
		super().__init__(parent)
		self._func = func

	def run(self) -> None:
		try:
			self._func(self.progress.emit)
		except Exception as error:  # noqa: BLE001
			self.failed.emit(str(error))
		else:
			self.completed.emit()


class ArchiveThread(QThread):
	completed = Signal(str)
	failed = Signal(str)

	def __init__(
		self,
		archive_path: Path,
		base_path: Path,
		paths: list[Path],
		overwrite: bool,
		password: str,
		parent=None,
	):
		super().__init__(parent)
		self.archive_path = archive_path
		self.base_path = base_path
		self.paths = paths
		self.overwrite = overwrite
		self.password = password

	def run(self) -> None:
		try:
			archive_path = create_archive(
				self.archive_path,
				self.base_path,
				self.paths,
				overwrite=self.overwrite,
				password=self.password or None,
			)
		except Exception as error:  # noqa: BLE001
			self.failed.emit(str(error))
		else:
			self.completed.emit(str(archive_path))


class ArchiveCopyThread(QThread):
	completed = Signal(int)
	failed = Signal(str)

	def __init__(
		self,
		archive_path: Path,
		entries: list[ArchiveEntry],
		destination: Path,
		password: str | None,
		parent=None,
	):
		super().__init__(parent)
		self.archive_path = archive_path
		self.entries = entries
		self.destination = destination
		self.password = password

	def run(self) -> None:
		try:
			copied_count = copy_archive_entries(
				self.archive_path,
				self.entries,
				self.destination,
				password=self.password,
				overwrite=True,
			)
		except Exception as error:  # noqa: BLE001
			self.failed.emit(str(error))
		else:
			self.completed.emit(copied_count)


class ExtractArchiveThread(QThread):
	completed = Signal(int, str)
	failed = Signal(str)
	progress = Signal(int, int)

	def __init__(
		self,
		archive_paths: list[Path],
		destination: Path,
		parent=None,
	):
		super().__init__(parent)
		self.archive_paths = archive_paths
		self.destination = destination

	def run(self) -> None:
		extracted_count = 0
		errors: list[str] = []
		total = len(self.archive_paths)
		for i, archive_path in enumerate(self.archive_paths):
			try:
				extract_archive(archive_path, self.destination)
			except Exception as error:  # noqa: BLE001
				errors.append(f"{archive_path.name}: {error}")
			else:
				extracted_count += 1
			self.progress.emit(i + 1, total)

		if extracted_count == 0:
			self.failed.emit("\n".join(errors) or "展開できるアーカイブがありません。")
		else:
			self.completed.emit(extracted_count, "\n".join(errors))


class SearchThread(QThread):
	results_ready = Signal(list)
	error = Signal(str)

	def __init__(self, root: Path, query: str, parent=None):
		super().__init__(parent)
		self.root = root
		self.query = query

	def run(self) -> None:
		try:
			results = find_paths(
				self.root,
				self.query,
				is_cancelled=self.isInterruptionRequested,
			)
		except OSError as error:
			self.error.emit(str(error))
		else:
			self.results_ready.emit(results)


# ── フォルダサイズ計算スレッド ────────────────────────────


class DirSizeThread(QThread):
    finished_with = Signal(list)   # list of (path, bytes, count)
    progress = Signal(str)

    def __init__(self, paths: list[Path], parent=None):
        super().__init__(parent)
        self._paths = list(paths)

    def run(self) -> None:
        from ore_filer.services.file_operations import calc_dir_size
        results: list[tuple[Path, int, int]] = []
        for path in self._paths:
            self.progress.emit(f"計算中: {path.name}")
            total_bytes, total_files = calc_dir_size(path, self.isInterruptionRequested)
            results.append((path, total_bytes, total_files))
        self.finished_with.emit(results)


# ── GREP ──────────────────────────────────────────────────────


class GrepThread(QThread):
    match_found = Signal(object)   # GrepMatch
    finished_grep = Signal(list)   # list[GrepMatch]
    error = Signal(str)

    def __init__(
        self,
        root: Path,
        pattern: str,
        *,
        recursive: bool = True,
        use_regex: bool = False,
        ignore_case: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.root = root
        self.pattern = pattern
        self.recursive = recursive
        self.use_regex = use_regex
        self.ignore_case = ignore_case

    def run(self) -> None:
        try:
            results = grep_files(
                self.root,
                self.pattern,
                recursive=self.recursive,
                use_regex=self.use_regex,
                ignore_case=self.ignore_case,
                is_cancelled=self.isInterruptionRequested,
            )
        except Exception as e:  # noqa: BLE001
            self.error.emit(str(e))
        else:
            self.finished_grep.emit(results)


# ── Git ───────────────────────────────────────────────────────


class GitStatusThread(QThread):
    """フォルダの Git 状態をバックグラウンドで取得するスレッド。

    finished_with を emit するとき:
      - pane_path: スレッド起動時のペインパス（文字列）
      - branch: ブランチ名（リポジトリ外は None）
      - status_map: {casefolded パス文字列: XY コード}
    """
    finished_with = Signal(str, object, dict)  # (pane_path, branch|None, status_map)

    def __init__(self, pane_path: Path, parent=None):
        super().__init__(parent)
        self._pane_path = pane_path

    def run(self) -> None:
        pane_str = str(self._pane_path)
        try:
            repo = git_service.find_repo_root(self._pane_path)
            if repo is None:
                self.finished_with.emit(pane_str, None, {})
                return
            branch = git_service.current_branch(repo)
            raw_map = git_service.status(repo)
            str_map = {str(k).casefold(): v for k, v in raw_map.items()}
            self.finished_with.emit(pane_str, branch, str_map)
        except Exception:  # noqa: BLE001
            self.finished_with.emit(pane_str, None, {})


class GitCommandThread(QThread):
    """任意の Git コマンドをバックグラウンドで実行するスレッド。"""
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self._func = func

    def run(self) -> None:
        try:
            result = self._func()
            self.succeeded.emit(result or "")
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class RemoteConnectThread(QThread):
    """SSH 接続と開始フォルダの解決をUIスレッド外で行う。"""

    succeeded = Signal(object, str)  # (RemoteSession, 開始フォルダ)
    failed = Signal(str)

    def __init__(self, profile: RemoteProfile, password: str | None, parent=None) -> None:
        super().__init__(parent)
        self._profile = profile
        self._password = password

    def run(self) -> None:
        try:
            session = RemoteSession(self._profile, password=self._password)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(str(error) or type(error).__name__)
            return
        try:
            start = self._profile.initial_path or session.home_dir()
        except Exception as error:  # noqa: BLE001
            session.close()
            self.failed.emit(str(error) or type(error).__name__)
            return
        self.succeeded.emit(session, start)


class RemoteListThread(QThread):
    """リモートフォルダの一覧取得をUIスレッド外で行う。"""

    listed = Signal(int, str, object)  # (request_id, path, list[RemoteEntry])
    failed = Signal(int, str)

    def __init__(
        self,
        session: RemoteSession,
        path: str,
        request_id: int,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._path = path
        self._request_id = request_id

    def run(self) -> None:
        try:
            entries = self._session.list_dir(self._path)
        except Exception as error:  # noqa: BLE001
            self.failed.emit(self._request_id, str(error) or type(error).__name__)
            return
        self.listed.emit(self._request_id, self._path, entries)
