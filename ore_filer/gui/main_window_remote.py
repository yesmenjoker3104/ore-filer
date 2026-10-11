from __future__ import annotations

from PySide6.QtWidgets import QDialog, QMessageBox

from ore_filer.gui.remote_dialog import RemoteConnectDialog
from ore_filer.gui.threads import RemoteConnectThread
from ore_filer.services.remote_service import RemoteSession


class MainWindowRemoteMixin:
	"""リモート接続の開始と切断。"""


	def connect_remote(self) -> None:
		if self._remote_connect_thread is not None and self._remote_connect_thread.isRunning():
			return
		dialog = RemoteConnectDialog(self)
		if dialog.exec() != QDialog.DialogCode.Accepted:
			return
		profile, password = dialog.values()
		self._remote_target_pane = self.active_pane
		self.statusBar().showMessage(f"{profile.name} に接続中...")
		thread = RemoteConnectThread(profile, password, self)
		thread.succeeded.connect(self._on_remote_connected)
		thread.failed.connect(self._on_remote_connect_failed)
		thread.finished.connect(thread.deleteLater)
		thread.finished.connect(self._clear_remote_connect_thread)
		self._remote_connect_thread = thread
		thread.start()


	def _clear_remote_connect_thread(self) -> None:
		self._remote_connect_thread = None


	def _on_remote_connected(self, session: RemoteSession, start: str) -> None:
		pane = self._remote_target_pane or self.active_pane
		self._remote_target_pane = None
		pane.open_remote(session, start)
		self.statusBar().showMessage(f"接続しました: {session.profile.name}", 5000)


	def _on_remote_connect_failed(self, message: str) -> None:
		self._remote_target_pane = None
		self.statusBar().clearMessage()
		QMessageBox.critical(self, "リモート接続エラー", message)


	def _show_remote_error(self, message: str) -> None:
		QMessageBox.critical(self, "リモートエラー", message)


	def disconnect_remote(self) -> None:
		pane = self.active_pane
		if not pane.is_remote_view():
			self.statusBar().showMessage("リモート接続はありません", 3000)
			return
		name = pane.remote_session().profile.name
		pane.navigate_to(pane.current_path, record_history=False)
		self.statusBar().showMessage(f"切断しました: {name}", 5000)
