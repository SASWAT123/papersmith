"""Small cross-run preference store backed by the project SQLite database."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


class PreferenceMemory:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = Path(
            db_path or os.environ.get("PAPERSMITH_SQLITE_PATH", "data/papersmith.db")
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS preferences "
                "(name TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )

    def set(self, name: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO preferences(name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (name, value),
            )

    def get(self, name: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM preferences WHERE name = ?", (name,)
            ).fetchone()
        return row[0] if row else None

    def all(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT name, value FROM preferences").fetchall()
        return dict(rows)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)
