from ore_filer.gui import threads
from ore_filer.gui.threads import RemoteConnectThread, RemoteListThread
from ore_filer.services.remote_service import RemoteError, RemoteProfile


def _profile() -> RemoteProfile:
    return RemoteProfile(name="t", host="127.0.0.1", port=2222, username="u")


def test_connect_thread_reports_unexpected_exception_as_failed(monkeypatch) -> None:
    def boom(*_args, **_kwargs):
        raise ValueError("unexpected")

    monkeypatch.setattr(threads, "RemoteSessionProcess", boom)
    thread = RemoteConnectThread(_profile(), None)
    failures: list[str] = []
    successes: list[object] = []
    thread.failed.connect(failures.append)
    thread.succeeded.connect(lambda *args: successes.append(args))

    thread.run()

    assert failures == ["unexpected"]
    assert successes == []


def test_connect_thread_reports_remote_error_as_failed(monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise RemoteError("refused")

    monkeypatch.setattr(threads, "RemoteSessionProcess", refuse)
    thread = RemoteConnectThread(_profile(), None)
    failures: list[str] = []
    thread.failed.connect(failures.append)

    thread.run()

    assert failures == ["refused"]


def test_connect_thread_closes_session_when_home_dir_fails(monkeypatch) -> None:
    closed: list[bool] = []

    class FakeSession:
        def home_dir(self) -> str:
            raise KeyError("no home")

        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(threads, "RemoteSessionProcess", lambda *a, **k: FakeSession())
    thread = RemoteConnectThread(RemoteProfile(name="t", host="h", username="u", initial_path=""), None)
    failures: list[str] = []
    thread.failed.connect(failures.append)

    thread.run()

    assert closed == [True]
    assert failures == ["'no home'"]


def test_list_thread_reports_unexpected_exception_as_failed() -> None:
    class BrokenSession:
        def list_dir(self, _path: str):
            raise RuntimeError("list broke")

    thread = RemoteListThread(BrokenSession(), "/", 7)
    failures: list[tuple[int, str]] = []
    thread.failed.connect(lambda rid, msg: failures.append((rid, msg)))

    thread.run()

    assert failures == [(7, "list broke")]
