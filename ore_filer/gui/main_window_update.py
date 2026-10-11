from __future__ import annotations

from datetime import UTC

from PySide6.QtWidgets import QMessageBox

from ore_filer.settings import load_session
from ore_filer.updater import UpdateCheckThread, UpdateDownloadThread, install_update


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
			f"新しいバージョン {tag} があります。ダウンロードしますか？",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
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
			install_update(path, launcher_path)
			QMessageBox.information(
				self,
				"アップデート完了",
				"アップデートを適用しました。\nアプリを再起動してください。",
				QMessageBox.StandardButton.Ok,
			)
			self.close()
		else:
			self.statusBar().showMessage("開発モードのためインストールをスキップしました")
