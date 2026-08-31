from types import SimpleNamespace

from streamforge.workers import processor
from streamforge.workers.notifications import (
    NEW_JOBS_CHANNEL,
    JobNotificationListener,
    psycopg_dsn,
)


class FakeConnection:
    def __init__(self, notifications=()) -> None:
        self.closed = False
        self.executed = []
        self._notifications = notifications

    def execute(self, statement: str) -> None:
        self.executed.append(statement)

    def notifies(self, *, timeout: float, stop_after: int):
        del timeout, stop_after
        yield from self._notifications

    def close(self) -> None:
        self.closed = True


def test_listener_receives_new_job_notification() -> None:
    connection = FakeConnection([SimpleNamespace(payload="job-id")])
    calls = []

    def connect(dsn: str, *, autocommit: bool):
        calls.append((dsn, autocommit))
        return connection

    listener = JobNotificationListener(
        "postgresql+psycopg://user:secret@database:5432/streamforge",
        connection_factory=connect,
    )

    assert listener.start() is True
    assert listener.wait(2.0) is True
    assert calls == [("postgresql://user:secret@database:5432/streamforge", True)]
    assert connection.executed == [f"LISTEN {NEW_JOBS_CHANNEL}"]


def test_listener_failure_waits_for_polling_fallback(monkeypatch) -> None:
    sleeps = []

    def unavailable(*_args, **_kwargs):
        raise OSError("database unavailable")

    listener = JobNotificationListener(
        "postgresql+psycopg://user:secret@database/streamforge",
        connection_factory=unavailable,
    )
    monkeypatch.setattr("streamforge.workers.notifications.time.sleep", sleeps.append)

    assert listener.wait(1.5) is False
    assert sleeps == [1.5]


def test_disabled_notifications_use_polling_fallback(monkeypatch) -> None:
    sleeps = []
    monkeypatch.setattr(processor.time, "sleep", sleeps.append)

    assert processor.wait_for_new_job(None, 0.5) is False
    assert sleeps == [0.5]


def test_psycopg_dsn_removes_sqlalchemy_driver_name() -> None:
    assert (
        psycopg_dsn("postgresql+psycopg://user:secret@database/streamforge")
        == "postgresql://user:secret@database/streamforge"
    )
