from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
	QDialog,
	QDialogButtonBox,
	QListWidget,
    QMainWindow,
    QSplitter,
	QMessageBox,
	QInputDialog,
	QVBoxLayout,
)

from ore_filer.gui.pane import PaneWidget
from ore_filer.settings import save_session
from ore_filer.services.file_operations import (
	copy_paths,
	create_directory,
	delete_paths,
	rename_path,
	move_paths,
)


class MainWindow(QMainWindow):
	def __init__(
		self,
		left_path: str | Path,
		right_path: str | Path,
		history: list[str | Path] | None = None,
	):
		super().__init__()
		self.setWindowTitle("Ore Filer")
		self.resize(1200, 700)
		shared_history = history if history is not None else []
		self.left_pane = PaneWidget(left_path, history=shared_history)
		self.right_pane = PaneWidget(right_path, history=shared_history)

		self.panes = [self.left_pane, self.right_pane]
		self.active_pane_index = 0

		splitter = QSplitter(Qt.Orientation.Horizontal)
		splitter.addWidget(self.left_pane)
		splitter.addWidget(self.right_pane)
		splitter.setSizes([600, 600])

		self.setCentralWidget(splitter)

		for pane in self.panes:
			pane.file_view.installEventFilter(self)
			pane.file_view.viewport().installEventFilter(self)

		self._update_active_pane()

	@property
	def active_pane(self) -> PaneWidget:
		return self.panes[self.active_pane_index]

	@property
	def inactive_pane(self) -> PaneWidget:
		return self.panes[1 - self.active_pane_index]

	def focused_path(self) -> Path | None:
		index = self.active_pane.file_view.currentIndex()
		if not index.isValid():
			return None
		return Path(self.active_pane.model.filePath(index))

	def closeEvent(self, event) -> None:
		save_session(self.left_pane.history_paths())
		super().closeEvent(event)

	def show_history(self) -> None:
		history = self.active_pane.history_paths()
		if not history:
			return

		dialog = QDialog(self)
		dialog.setWindowTitle("履歴")
		list_widget = QListWidget(dialog)
		for path in history:
			list_widget.addItem(str(path))
		list_widget.setCurrentRow(0)

		buttons = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Ok
			| QDialogButtonBox.StandardButton.Cancel,
			parent=dialog,
		)
		buttons.accepted.connect(dialog.accept)
		buttons.rejected.connect(dialog.reject)
		list_widget.itemDoubleClicked.connect(lambda _item: dialog.accept())

		layout = QVBoxLayout(dialog)
		layout.addWidget(list_widget)
		layout.addWidget(buttons)

		if dialog.exec() == QDialog.DialogCode.Accepted:
			selected = list_widget.currentRow()
			if selected >= 0:
				self.active_pane.navigate_to(history[selected])

	def confirm_overwrite(self, paths: list[Path], destination: Path) -> bool:
		existing = [destination / path.name for path in paths
					if (destination / path.name).exists()]
		if not existing:
			return True

		names = "\n".join(path.name for path in existing)
		confirm = QMessageBox.question(
			self,
			"上書き確認",
			f"次の項目は既に存在します。上書きしますか？\n\n{names}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		return confirm == QMessageBox.StandardButton.Yes

	def copy_selected(self) -> None:
		source = self.active_pane.selected_paths()
		if not source:
			return

		destination = self.inactive_pane.current_path
		names = "\n".join(path.name for path in source)

		confirm = QMessageBox.question(
			self,
			"ファイルコピー確認",
			f"次の項目をコピーしますか？\n\n{destination}\n\n{names}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
		)

		if confirm == QMessageBox.StandardButton.Yes:
			if not self.confirm_overwrite(source, destination):
				return
			copy_paths(source, destination, overwrite=True)
			self.active_pane.file_view.selectionModel().clearSelection()
			self.inactive_pane.reload()
		else:
			return
		
	def create_folder(self) -> None:
		folder_name, ok = QInputDialog.getText(
			self,
			"新規フォルダの作成",
			"フォルダ名:"
		)
		if ok and folder_name:
			new_folder_path = self.active_pane.current_path / folder_name
			if new_folder_path.exists():
				QMessageBox.warning(
					self,
					"警告",
					"同名のフォルダ、またはファイルが存在します。"
				)
				return
			try:
				create_directory(self.active_pane.current_path, folder_name)
			except Exception as e:
				QMessageBox.critical(
					self,
					"エラー",
					f"フォルダの作成に失敗しました:\n{e}"
				)
				return
			self.active_pane.reload()

	def rename_item(self) -> None:
		index = self.active_pane.file_view.currentIndex()
		if not index.isValid():
			return

		old_path = Path(self.active_pane.model.filePath(index))
		new_name, ok = QInputDialog.getText(
			self,
			"名前の変更",
			"新しい名前:",
			text=old_path.name
		)
		if ok and new_name:
			new_path = old_path.parent / new_name
			if new_path.exists():
				QMessageBox.warning(
					self,
					"警告",
					"同名のフォルダ、またはファイルが存在します。"
				)
				return
			try:
				rename_path(old_path, new_name)
			except Exception as e:
				QMessageBox.critical(
					self,
					"エラー",
					f"名前の変更に失敗しました:\n{e}"
				)
				return
			self.active_pane.reload()

	def delete_selected(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return

		confirm = QMessageBox.question(
			self,
			"削除確認",
			f"次の項目を削除しますか？\n\n{chr(10).join(path.name for path in paths)}",
			QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
			QMessageBox.StandardButton.No,
		)
		if confirm != QMessageBox.StandardButton.Yes:
			return

		try:
			delete_paths(paths)
		except OSError as error:
			QMessageBox.critical(self, "削除エラー", str(error))
			return

		self.active_pane.file_view.selectionModel().clearSelection()
		self.active_pane.reload()

	def move_selected(self) -> None:
		paths = self.active_pane.selected_paths()
		if not paths:
			return

		destination = self.inactive_pane.current_path
		if not self.confirm_overwrite(paths, destination):
			return

		try:
			move_paths(paths, destination, overwrite=True)
		except Exception as error:
			QMessageBox.critical(
				self,
				"エラー",
				f"移動に失敗しました:\n{error}"
			)
			return

		self.active_pane.file_view.selectionModel().clearSelection()
		self.active_pane.reload()
		self.inactive_pane.reload()

	def eventFilter(self, watched, event) -> bool:
		if event.type() == QEvent.Type.KeyPress:
			if (event.key() == Qt.Key.Key_Tab) or \
				(event.key() == Qt.Key.Key_Right and self.active_pane_index == 0) or \
				(event.key() == Qt.Key.Key_Left and self.active_pane_index == 1):
				self._switch_pane()
				return True
			
			elif event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
				path = self.focused_path()
				if path is not None and path.is_dir():
					self.active_pane.navigate_to(path)
					return True
				
			elif (event.key() == Qt.Key.Key_Left and self.active_pane_index == 0) or \
				(event.key() == Qt.Key.Key_Right and self.active_pane_index == 1):
				self.active_pane.go_to_parent()
				return True
			
			elif event.key() == Qt.Key.Key_T:
				index = self.active_pane.file_view.currentIndex()
				if index.isValid() and self.active_pane.model.isDir(index):
					file_view = self.active_pane.file_view

					if file_view.isExpanded(index):
						file_view.collapse(index)
					else:
						file_view.expand(index)

					return True
			elif event.key() == Qt.Key.Key_F5:
				self.active_pane.reload()
				return True
			elif event.key() == Qt.Key.Key_C:
				self.copy_selected()
				return True
			elif event.key() == Qt.Key.Key_Space:
				self.active_pane.select_current_and_move_down()
				return True
			elif event.key() == Qt.Key.Key_M:
				if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
					self.move_selected()
				else:
					self.create_folder()
				return True
			elif event.key() == Qt.Key.Key_R:
				self.rename_item()
				return True
			elif event.key() == Qt.Key.Key_H:
				self.show_history()
				return True
			elif event.key() == Qt.Key.Key_K:
				self.delete_selected()
				return True
		return super().eventFilter(watched, event)

	def _switch_pane(self) -> None:
		self.active_pane_index = 1 - self.active_pane_index
		self._update_active_pane()

	def _update_active_pane(self) -> None:
		active_pane = self.active_pane
		for pane in self.panes:
			pane.setStyleSheet("")
		active_pane.setStyleSheet("border: 1px solid #4b6584;")
		active_pane.file_view.setFocus()
