"""SQLAlchemy engine/session setup.

SQLite is used locally. Nothing here is SQLite-specific except the connection
pragmas, so switching TPA_DATABASE_URL to PostgreSQL only requires a driver.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from tennis_api.config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings) -> None:
        url = settings.resolved_database_url
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            settings.storage_dir.mkdir(parents=True, exist_ok=True)
            connect_args = {"check_same_thread": False, "timeout": 30}
        self.engine: Engine = create_engine(url, connect_args=connect_args)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        from tennis_api import models  # noqa: F401 - register tables

        Base.metadata.create_all(self.engine)

    def migrate(self) -> list[str]:
        """Create missing tables, then apply in-place migrations (API startup only)."""
        from tennis_api.migrations import run_migrations

        self.create_all()
        return run_migrations(self.engine)

    def session(self) -> Session:
        return self.session_factory()

    def session_scope(self) -> Iterator[Session]:
        with self.session_factory() as s:
            yield s


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cur = dbapi_conn.cursor()
    # WAL lets the API read while the worker writes (two processes).
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.close()
