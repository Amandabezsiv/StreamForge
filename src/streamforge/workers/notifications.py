import logging
import time
from collections.abc import Callable
from typing import Any

import psycopg
from sqlalchemy.engine import make_url

logger = logging.getLogger("streamforge.worker.notifications")

NEW_JOBS_CHANNEL = "streamforge_new_jobs"


def psycopg_dsn(database_url: str) -> str:
    url = make_url(database_url)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


class JobNotificationListener:
    """Maintain the dedicated PostgreSQL connection required by LISTEN."""

    def __init__(
        self,
        database_url: str,
        connection_factory: Callable[..., Any] = psycopg.connect,
    ) -> None:
        self._dsn = psycopg_dsn(database_url)
        self._connection_factory = connection_factory
        self._connection: Any | None = None

    def start(self) -> bool:
        try:
            self._ensure_connected()
        except (OSError, psycopg.Error):
            self.close()
            logger.exception("could not start PostgreSQL job notification listener")
            return False
        return True

    def wait(self, timeout: float) -> bool:
        """Wait for one notification, using the timeout as polling fallback."""
        try:
            self._ensure_connected()
            notifications = self._connection.notifies(timeout=timeout, stop_after=1)
            return next(notifications, None) is not None
        except (OSError, psycopg.Error):
            self.close()
            logger.exception("job notification listener failed; using polling fallback")
            time.sleep(timeout)
            return False

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def _ensure_connected(self) -> None:
        if self._connection is not None and not self._connection.closed:
            return
        self._connection = self._connection_factory(self._dsn, autocommit=True)
        self._connection.execute(f"LISTEN {NEW_JOBS_CHANNEL}")
