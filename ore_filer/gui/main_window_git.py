from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox

from ore_filer.gui.dialogs.diff_dialogs import DiffDialog, SideBySideDiffDialog
from ore_filer.gui.dialogs.git_dialogs import (
	GitCommitDialog,
	GitHttpCredentialsDialog,
	GitLogDialog,
)
from ore_filer.gui.pane import PaneWidget
from ore_filer.gui.threads import GitCommandThread, GitStatusThread
from ore_filer.services import git_service


class MainWindowGitMixin:
	"""Git 状態の表示と Git 操作。"""


	# ── Git 操作 ─────────────────────────────────────────────

	def _refresh_git_status(self, pane: PaneWidget) -> None:
		"""バックグラウンドで Git 状態を取得してペインに反映する。"""
		if not git_service.is_available():
			pane.set_git_info(None, {})
			return
		pane_id = id(pane)
		pane_path = pane.current_path
		thread = GitStatusThread(pane_path, self)
		thread.finished_with.connect(
			lambda result_path, branch, status, p=pane, pp=str(pane_path):
				self._on_git_status_ready(p, pp, result_path, branch, status)
		)

		def _cleanup(pid=pane_id, t=thread):
			if self._git_status_threads.get(pid) is t:
				del self._git_status_threads[pid]
			t.deleteLater()

		thread.finished.connect(_cleanup)
		self._git_status_threads[pane_id] = thread
		thread.start()


	def _on_git_status_ready(
		self,
		pane: PaneWidget,
		expected_path: str,
		result_path: str,
		branch,
		status_map: dict,
	) -> None:
		"""GitStatusThread の完了時コールバック。"""
		if str(pane.current_path) != expected_path:
			return  # ペインが既に別のパスへ移動済み
		pane.set_git_info(branch, status_map)


	def _git_repo(self) -> Path | None:
		"""アクティブペインの Git リポジトリルートを返す。なければ None。"""
		if not git_service.is_available():
			return None
		return git_service.find_repo_root(self.active_pane.current_path)


	def show_git_menu(self) -> None:
		"""G: Git ウィンドウを開く。"""
		from ore_filer.gui.git_window import GitWindow

		if not git_service.is_available():
			QMessageBox.information(
				self,
				"Git",
				"Git機能を利用できません。Dulwichがインストールされていません。",
			)
			return
		repo = git_service.find_repo_root(self.active_pane.current_path)
		if repo is None:
			QMessageBox.information(self, "Git", "Git リポジトリではありません。")
			return

		win = self._git_windows.get(repo)
		if win is None:
			win = GitWindow(repo, self)
			win.repo_changed.connect(self._on_git_repo_changed)
			self._git_windows[repo] = win

		win.show()
		win.raise_()
		win.activateWindow()
		win._refresh()


	def _on_git_repo_changed(self) -> None:
		for pane in self.panes:
			self._refresh_git_status(pane)


	def git_stage(self) -> None:
		"""選択/カーソル項目をステージする。"""
		repo = self._git_repo()
		if repo is None:
			return
		paths = self.active_pane.selected_paths()
		if not paths:
			path = self.focused_path()
			if path is None:
				return
			paths = [path]
		try:
			git_service.stage(repo, paths)
		except Exception as e:  # noqa: BLE001
			QMessageBox.warning(self, "Git ステージ", f"ステージに失敗しました:\n{e}")
			return
		self._refresh_git_status(self.active_pane)
		self.statusBar().showMessage(f"{len(paths)} 件をステージしました", 3000)


	def git_unstage(self) -> None:
		"""選択/カーソル項目のステージを解除する。"""
		repo = self._git_repo()
		if repo is None:
			return
		paths = self.active_pane.selected_paths()
		if not paths:
			path = self.focused_path()
			if path is None:
				return
			paths = [path]
		try:
			git_service.unstage(repo, paths)
		except Exception as e:  # noqa: BLE001
			QMessageBox.warning(self, "Git ステージ解除", f"ステージ解除に失敗しました:\n{e}")
			return
		self._refresh_git_status(self.active_pane)
		self.statusBar().showMessage(f"{len(paths)} 件のステージを解除しました", 3000)


	def git_commit(self) -> None:
		"""ステージ済みファイルをコミットするダイアログを開く。"""
		repo = self._git_repo()
		if repo is None:
			return
		try:
			raw_map = git_service.status(repo)
		except Exception as e:  # noqa: BLE001
			QMessageBox.warning(self, "Git コミット", str(e))
			return

		staged: list[str] = []
		for path, xy in raw_map.items():
			if (
				len(xy) >= 1
				and xy[0] not in (" ", "?", "!")
				and xy != "D~"
				and path.is_file()
			):
				try:
					staged.append(str(path.relative_to(repo)))
				except ValueError:
					staged.append(path.name)

		if not staged:
			QMessageBox.information(self, "Git コミット", "ステージされた変更がありません。")
			return

		dialog = GitCommitDialog(staged, self)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		msg = dialog.commit_message()
		if not msg:
			return

		thread = GitCommandThread(lambda r=repo, m=msg: git_service.commit(r, m), self)
		thread.succeeded.connect(lambda out: self._on_git_command_done("コミット", out))
		thread.failed.connect(lambda err: self._on_git_command_failed("コミット", err))
		thread.finished.connect(thread.deleteLater)
		thread.start()


	def _http_credentials(self, repo) -> tuple[str, str] | None:
		"""HTTPリモートの場合に認証ダイアログを表示してcredentialsを返す。不要なら(None,None)相当。"""
		from ore_filer import credentials as cred_store
		url = git_service.get_remote_url(repo)
		if not (url and url.startswith(("http://", "https://"))):
			return ("", "")
		saved = cred_store.load("git", url)
		username, password = saved if saved else ("", "")
		dlg = GitHttpCredentialsDialog(url, username=username, password=password, parent=self)
		if dlg.exec() != GitHttpCredentialsDialog.DialogCode.Accepted:
			return None
		username, password = dlg.credentials()
		if dlg.should_save():
			cred_store.save("git", url, username, password)
		else:
			cred_store.delete("git", url)
		return username, password


	def git_fetch(self) -> None:
		"""リモートの参照を取得する。作業ツリーは変更しない。"""
		repo = self._git_repo()
		if repo is None:
			return
		confirm = QMessageBox.question(
			self, "git fetch", "リモートから最新の履歴を取得しますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return
		creds = self._http_credentials(repo)
		if creds is None:
			return
		username, password = creds
		self.statusBar().showMessage("git fetch 実行中...")
		thread = GitCommandThread(
			lambda r=repo, u=username, p=password: git_service.fetch(r, username=u or None, password=p or None),
			self,
		)
		thread.succeeded.connect(lambda out: self._on_git_sync_done("fetch", out))
		thread.failed.connect(lambda err: self._on_git_command_failed("fetch", err))
		thread.finished.connect(thread.deleteLater)
		thread.start()


	def git_pull(self) -> None:
		"""git pull を実行する。"""
		repo = self._git_repo()
		if repo is None:
			return
		confirm = QMessageBox.question(
			self, "git pull", "リモートから変更を取得しますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return
		creds = self._http_credentials(repo)
		if creds is None:
			return
		username, password = creds
		self.statusBar().showMessage("git pull 実行中...")
		thread = GitCommandThread(
			lambda r=repo, u=username, p=password: git_service.pull(r, username=u or None, password=p or None),
			self,
		)
		thread.succeeded.connect(lambda out: self._on_git_sync_done("pull", out))
		thread.failed.connect(lambda err: self._on_git_command_failed("pull", err))
		thread.finished.connect(thread.deleteLater)
		thread.start()


	def git_push(self) -> None:
		"""git push を実行する。"""
		repo = self._git_repo()
		if repo is None:
			return
		confirm = QMessageBox.question(
			self, "git push", "リモートへ変更を送信しますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return
		creds = self._http_credentials(repo)
		if creds is None:
			return
		username, password = creds
		self.statusBar().showMessage("git push 実行中...")
		thread = GitCommandThread(
			lambda r=repo, u=username, p=password: git_service.push(r, username=u or None, password=p or None),
			self,
		)
		thread.succeeded.connect(lambda out: self._on_git_sync_done("push", out))
		thread.failed.connect(lambda err: self._on_git_command_failed("push", err))
		thread.finished.connect(thread.deleteLater)
		thread.start()


	def git_diff(self) -> None:
		"""カーソルファイルの git diff HEAD を表示する。"""
		repo = self._git_repo()
		if repo is None:
			return
		path = self.focused_path()
		if path is None or path.is_dir():
			QMessageBox.information(self, "Git 差分", "ファイルを選択してください。")
			return
		try:
			head_text = git_service.head_file_text(repo, path)
			work_text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
		except Exception as e:  # noqa: BLE001
			QMessageBox.warning(self, "Git 差分", str(e))
			return
		SideBySideDiffDialog("HEAD", path.name, head_text, work_text, self).exec()


	def git_log(self) -> None:
		"""Git ログを表示し、選択したコミットの差分を表示する。"""
		repo = self._git_repo()
		if repo is None:
			return
		try:
			entries = git_service.log(repo)
		except Exception as e:  # noqa: BLE001
			QMessageBox.warning(self, "Git ログ", str(e))
			return
		if not entries:
			QMessageBox.information(self, "Git ログ", "コミット履歴がありません。")
			return

		dialog = GitLogDialog(entries, self)
		if dialog.exec() == QDialog.DialogCode.Accepted:
			commit_hash = dialog.selected_commit()
			if commit_hash:
				try:
					diff_text = git_service.show(repo, commit_hash)
					DiffDialog(commit_hash, "show", diff_text, self).exec()
				except Exception as e:  # noqa: BLE001
					QMessageBox.warning(self, "git show", str(e))


	def git_switch_branch(self) -> None:
		"""ブランチ切替ダイアログを開く。"""
		repo = self._git_repo()
		if repo is None:
			return
		try:
			branch_list = git_service.branches(repo)
		except Exception as e:  # noqa: BLE001
			QMessageBox.warning(self, "ブランチ切替", str(e))
			return
		if not branch_list:
			QMessageBox.information(self, "ブランチ切替", "ブランチが見つかりません。")
			return

		current = branch_list[0] if branch_list else ""
		selected, ok = QInputDialog.getItem(
			self, "ブランチ切替", "切り替え先のブランチ:", branch_list, 0, False,
		)
		if not ok or selected == current:
			return

		confirm = QMessageBox.question(
			self, "ブランチ切替",
			f"ブランチを '{selected}' に切り替えますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return
		try:
			git_service.switch(repo, selected)
		except Exception as e:  # noqa: BLE001
			QMessageBox.warning(self, "ブランチ切替", f"切り替えに失敗しました:\n{e}")
			return
		for pane in self.panes:
			self._refresh_git_status(pane)
		self.statusBar().showMessage(f"ブランチを '{selected}' に切り替えました", 5000)


	def git_create_branch(self) -> None:
		"""新規ブランチを作成して切り替える。"""
		repo = self._git_repo()
		if repo is None:
			return
		name, ok = QInputDialog.getText(self, "新規ブランチ作成", "ブランチ名:")
		if not ok or not name.strip():
			return
		name = name.strip()
		try:
			git_service.create_branch(repo, name, checkout=True)
		except Exception as e:  # noqa: BLE001
			QMessageBox.warning(self, "新規ブランチ作成", f"失敗しました:\n{e}")
			return
		for pane in self.panes:
			self._refresh_git_status(pane)
		self.statusBar().showMessage(f"ブランチ '{name}' を作成して切り替えました", 5000)

	def _on_git_command_done(self, op: str, output: str) -> None:
		for pane in self.panes:
			self._refresh_git_status(pane)
		self.statusBar().showMessage(f"Git {op} 完了", 5000)


	def _on_git_sync_done(self, op: str, output: str) -> None:
		for pane in self.panes:
			pane.reload()
			self._refresh_git_status(pane)
		self.statusBar().showMessage(f"git {op} 完了", 5000)
		if output.strip():
			QMessageBox.information(self, f"git {op}", output.strip())


	def _on_git_command_failed(self, op: str, error: str) -> None:
		self.statusBar().clearMessage()
		QMessageBox.warning(self, f"Git {op} エラー", error)
