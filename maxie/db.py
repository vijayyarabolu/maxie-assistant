"""One SQLite file for the hub. Each module creates its own tables on first use."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

# Bump when a table or column is added or changed (each module migrates itself at init); the hub takes a
# backup before its first start on a new version.
SCHEMA_VERSION = 4  # 1: cloud; 2: models tables, route_log.laya_route/laya_p; 3: turns (latency); 4: flows


class DB:
    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def script(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None

    def add_columns(self, table: str, columns: dict[str, str]) -> None:
        """Add the columns an older table is missing ({name: SQL type}); a no-op once they exist."""
        have = {r["name"] for r in self.all(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                self.script(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
