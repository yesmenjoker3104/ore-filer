from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ore_filer import credentials as cred_store
from ore_filer import settings
from ore_filer.services.remote_service import RemoteProfile

CREDENTIAL_CATEGORY = "sftp"
NEW_PROFILE_LABEL = "（新規接続）"


def _credential_key(host: str, port: int) -> str:
    return f"{host}:{port}"


def _profile_name(username: str, host: str, port: int) -> str:
    return f"{username}@{host}" if port == 22 else f"{username}@{host}:{port}"


class RemoteConnectDialog(QDialog):
    """SSH/SFTP の接続先を入力するダイアログ。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("リモート接続")
        self.setMinimumWidth(360)

        self._profiles: list[dict] = settings.load_remote_profiles()
        self._profile_combo = QComboBox()
        self._profile_combo.addItem(NEW_PROFILE_LABEL)
        for record in self._profiles:
            self._profile_combo.addItem(record["name"])
        self._delete_button = QPushButton("削除")
        self._delete_button.setEnabled(False)
        self._save_password = QCheckBox("パスワードを保存する")
        self._save_password.setEnabled(cred_store.is_available())

        self._host = QLineEdit()
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(22)
        self._username = QLineEdit()
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_filename = QLineEdit()
        self._key_filename.setPlaceholderText("空欄の場合はパスワード / ssh-agent")
        self._initial_path = QLineEdit()
        self._initial_path.setPlaceholderText("空欄の場合はホームフォルダ")

        form = QFormLayout()
        form.addRow("ホスト", self._host)
        form.addRow("ポート", self._port)
        form.addRow("ユーザー名", self._username)
        form.addRow("パスワード", self._password)
        form.addRow("秘密鍵", self._key_filename)
        form.addRow("開始パス", self._initial_path)
        form.addRow("", self._save_password)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        profile_row = QHBoxLayout()
        profile_row.addWidget(self._profile_combo, 1)
        profile_row.addWidget(self._delete_button)
        layout = QVBoxLayout(self)
        layout.addLayout(profile_row)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self._profile_combo.currentIndexChanged.connect(self._on_profile_selected)
        self._delete_button.clicked.connect(self._on_delete_profile)
        self._host.setFocus()

    def _selected_record(self) -> dict | None:
        index = self._profile_combo.currentIndex() - 1
        return self._profiles[index] if index >= 0 else None

    def _on_profile_selected(self, _index: int) -> None:
        record = self._selected_record()
        self._delete_button.setEnabled(record is not None)
        self._password.clear()
        if record is None:
            self._host.clear()
            self._port.setValue(22)
            self._username.clear()
            self._key_filename.clear()
            self._initial_path.clear()
            self._save_password.setChecked(False)
            return
        self._host.setText(record["host"])
        self._port.setValue(record["port"])
        self._username.setText(record["username"])
        self._key_filename.setText(record.get("key_filename") or "")
        self._initial_path.setText(record.get("initial_path") or "")
        self._save_password.setChecked(bool(record.get("save_password")) and cred_store.is_available())
        if self._save_password.isChecked():
            stored = cred_store.load(CREDENTIAL_CATEGORY, _credential_key(record["host"], record["port"]))
            if stored is not None:
                self._password.setText(stored[1])

    def _on_delete_profile(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        answer = QMessageBox.question(self, "削除確認", f"接続先 {record['name']} を削除しますか？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._profiles.remove(record)
        settings.save_remote_profiles(self._profiles)
        cred_store.delete(CREDENTIAL_CATEGORY, _credential_key(record["host"], record["port"]))
        self._profile_combo.removeItem(self._profile_combo.currentIndex())

    def _on_accept(self) -> None:
        if not self._host.text().strip() or not self._username.text().strip():
            return
        profile, password = self.values()
        self._store_profile(profile, password)
        self.accept()

    def _store_profile(self, profile: RemoteProfile, password: str | None) -> None:
        save_password = self._save_password.isChecked() and cred_store.is_available()
        key = _credential_key(profile.host, profile.port)
        if save_password:
            if password:
                cred_store.save(CREDENTIAL_CATEGORY, key, profile.username, password)
        else:
            cred_store.delete(CREDENTIAL_CATEGORY, key)
        record = {
            "name": profile.name,
            "host": profile.host,
            "port": profile.port,
            "username": profile.username,
            "key_filename": profile.key_filename,
            "initial_path": profile.initial_path,
            "save_password": save_password,
        }
        self._profiles = [r for r in self._profiles if r["name"] != profile.name]
        self._profiles.append(record)
        settings.save_remote_profiles(self._profiles)

    def values(self) -> tuple[RemoteProfile, str | None]:
        host = self._host.text().strip()
        username = self._username.text().strip()
        port = self._port.value()
        key = self._key_filename.text().strip() or None
        profile = RemoteProfile(
            name=_profile_name(username, host, port),
            host=host,
            username=username,
            port=port,
            key_filename=key,
            initial_path=self._initial_path.text().strip(),
        )
        password = self._password.text() or None
        if password is None and self._save_password.isChecked():
            stored = cred_store.load(CREDENTIAL_CATEGORY, _credential_key(host, port))
            if stored is not None:
                password = stored[1]
        return profile, password
