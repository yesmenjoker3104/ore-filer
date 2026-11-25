from __future__ import annotations

from datetime import UTC

from PySide6.QtWidgets import QMessageBox

from ore_filer.settings import load_session
from ore_filer.updater import (
	UpdateCheckThread,
	UpdateDownloadThread,
	start_update_process,
)


class MainWindowUpdateMixin:
	"""アップデートの確認とダウンロード。"""


	def _check_for_updates(self, force: bool = False) -> None:
		from datetime import datetime, timedelta

		if not force:
			session = load_session()
			last = session.get("last_update_check", "")
			self._last_update_check = last
			if last:
				try:
					last_dt = datetime.fromisoformat(last)
					if datetime.now(UTC) - last_dt < timedelta(hours=24):
						return
				except ValueError:
					pass

		self._last_update_check = datetime.now(UTC).isoformat()
		self._update_check_thread = UpdateCheckThread()
		self._update_check_thread.update_available.connect(self._on_update_available)
		if force:
			self._update_check_thread.up_to_date.connect(
				lambda: self.statusBar().showMessage("最新版です")
			)
			self._update_check_thread.check_failed.connect(
				lambda: self.statusBar().showMessage("アップデート確認に失敗しました")
			)
		self._update_check_thread.start()


	def _on_update_available(self, tag: str, url: str) -> None:
		answer = QMessageBox.question(
			self,
			"アップデート",
			f"新しいバージョン {tag} があります。\n"
			"ダウンロードして更新後、自動的に再起動しますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.Yes,
		)
		if answer != QMessageBox.StandardButton.Yes:
			return
		self.statusBar().showMessage("アップデートをダウンロード中...")
		self._update_download_thread = UpdateDownloadThread(url)
		self._update_download_thread.download_progress.connect(
			lambda p: self.statusBar().showMessage(f"ダウンロード中... {p}%")
		)
		self._update_download_thread.download_finished.connect(self._on_download_finished)
		self._update_download_thread.download_failed.connect(
			lambda e: self.statusBar().showMessage(f"ダウンロード失敗: {e}")
		)
		self._update_download_thread.start()


	def _on_download_finished(self, path: str, launcher_path: str) -> None:
		import sys

		if getattr(sys, "frozen", False):
			try:
				start_update_process(path, launcher_path)
			except Exception as error:  # noqa: BLE001
				QMessageBox.critical(
					self,
					"アップデート失敗",
					f"自動更新を開始できませんでした。\n{error}",
				)
				return
			self.statusBar().showMessage("アップデートを適用して再起動します...")
			self.close()
		else:
			self.statusBar().showMessage("開発モードのためインストールをスキップしました")
