import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from ore_filer import settings
from ore_filer.gui import remote_dialog
from ore_filer.gui.remote_dialog import RemoteConnectDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeCredentialStore:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.saved: dict[tuple[str, str], tuple[str, str]] = {}
        self.deleted: list[tuple[str, str]] = []

    def is_available(self) -> bool:
        return self.available

    def save(self, category: str, key: str, username: str, password: str) -> None:
        self.saved[(category, key)] = (username, password)

    def load(self, category: str, key: str):
        return self.saved.get((category, key))

    def delete(self, category: str, key: str) -> None:
        self.deleted.append((category, key))
        self.saved.pop((category, key), None)


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(monkeypatch):
    fake = FakeCredentialStore()
    monkeypatch.setattr(remote_dialog, "cred_store", fake)
    return fake


def _fill(
    dialog: RemoteConnectDialog,
    host="example.test",
    port=22,
    user="alice",
    password="",
    save=False,
):
    dialog._host.setText(host)
    dialog._port.setValue(port)
    dialog._username.setText(user)
    dialog._password.setText(password)
    dialog._save_password.setChecked(save)


def test_profiles_round_trip_and_defaults(appdata):
    records = [
        {"name": "alice@example.test", "host": "example.test", "username": "alice"}
    ]
    settings.save_remote_profiles(records)

    loaded = settings.load_remote_profiles()

    assert loaded == [
        {
            "name": "alice@example.test",
            "host": "example.test",
            "username": "alice",
            "port": 22,
            "key_filename": None,
            "initial_path": "",
            "save_password": False,
        }
    ]


def test_load_profiles_ignores_missing_or_broken_file(appdata):
    assert settings.load_remote_profiles() == []
    path = appdata / "ore-filer" / "remote_profiles.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert settings.load_remote_profiles() == []


def test_save_with_password_stores_secret_outside_json(qapp, appdata, store):
    dialog = RemoteConnectDialog()
    _fill(dialog, password="s3cret-pass", save=True)

    dialog._on_accept()

    assert store.saved[("sftp", "example.test:22")] == ("alice", "s3cret-pass")
    text = (appdata / "ore-filer" / "remote_profiles.json").read_text(encoding="utf-8")
    assert "s3cret-pass" not in text
    records = json.loads(text)
    assert records[0]["save_password"] is True
    assert records[0]["name"] == "alice@example.test"


def test_save_unchecked_does_not_store_and_clears_old_password(qapp, appdata, store):
    store.saved[("sftp", "example.test:22")] = ("alice", "old")
    dialog = RemoteConnectDialog()
    _fill(dialog, password="typed", save=False)

    dialog._on_accept()

    assert ("sftp", "example.test:22") not in store.saved
    assert store.deleted == [("sftp", "example.test:22")]
    records = settings.load_remote_profiles()
    assert records[0]["save_password"] is False


def test_selecting_saved_profile_fills_fields_and_password(qapp, appdata, store):
    settings.save_remote_profiles(
        [
            {
                "name": "alice@example.test:2222",
                "host": "example.test",
                "port": 2222,
                "username": "alice",
                "key_filename": None,
                "initial_path": "/home/alice",
                "save_password": True,
            }
        ]
    )
    store.saved[("sftp", "example.test:2222")] = ("alice", "stored-pw")

    dialog = RemoteConnectDialog()
    dialog._profile_combo.setCurrentIndex(1)

    assert dialog._host.text() == "example.test"
    assert dialog._port.value() == 2222
    assert dialog._username.text() == "alice"
    assert dialog._initial_path.text() == "/home/alice"
    assert dialog._password.text() == "stored-pw"
    assert dialog._save_password.isChecked() is True
    assert dialog._delete_button.isEnabled() is True


def test_values_falls_back_to_stored_password_when_field_empty(qapp, appdata, store):
    store.saved[("sftp", "example.test:22")] = ("alice", "from-store")
    dialog = RemoteConnectDialog()
    _fill(dialog, password="", save=True)

    profile, password = dialog.values()

    assert profile.host == "example.test"
    assert password == "from-store"


def test_values_without_save_does_not_use_store(qapp, appdata, store):
    store.saved[("sftp", "example.test:22")] = ("alice", "from-store")
    dialog = RemoteConnectDialog()
    _fill(dialog, password="", save=False)

    _, password = dialog.values()

    assert password is None


def test_password_checkbox_disabled_when_keyring_unavailable(
    qapp, appdata, monkeypatch
):
    monkeypatch.setattr(
        remote_dialog, "cred_store", FakeCredentialStore(available=False)
    )
    dialog = RemoteConnectDialog()

    assert dialog._save_password.isEnabled() is False


def test_delete_profile_removes_record_and_password(qapp, appdata, store, monkeypatch):
    settings.save_remote_profiles(
        [
            {
                "name": "alice@example.test",
                "host": "example.test",
                "port": 22,
                "username": "alice",
                "key_filename": None,
                "initial_path": "",
                "save_password": True,
            }
        ]
    )
    store.saved[("sftp", "example.test:22")] = ("alice", "pw")
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )

    dialog = RemoteConnectDialog()
    dialog._profile_combo.setCurrentIndex(1)
    dialog._on_delete_profile()

    assert settings.load_remote_profiles() == []
    assert ("sftp", "example.test:22") in store.deleted
    assert dialog._profile_combo.count() == 1


def test_delete_cancelled_keeps_profile(qapp, appdata, store, monkeypatch):
    settings.save_remote_profiles(
        [
            {
                "name": "alice@example.test",
                "host": "example.test",
                "port": 22,
                "username": "alice",
            }
        ]
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )

    dialog = RemoteConnectDialog()
    dialog._profile_combo.setCurrentIndex(1)
    dialog._on_delete_profile()

    assert len(settings.load_remote_profiles()) == 1
    assert store.deleted == []
