"""SQLite storage for the Hub. One file holds every project, so one instance serves them all."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  slug TEXT PRIMARY KEY, name TEXT NOT NULL, brand TEXT NOT NULL DEFAULT '{}',
  settings TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pillars (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(slug), key TEXT NOT NULL,
  name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', weight REAL NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active', UNIQUE(project, key));
CREATE TABLE IF NOT EXISTS clusters (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(slug),
  pillar_id INTEGER REFERENCES pillars(id), slug TEXT NOT NULL, name TEXT NOT NULL,
  theme TEXT NOT NULL DEFAULT '', keywords TEXT NOT NULL DEFAULT '[]', starts_on TEXT, ends_on TEXT,
  origin TEXT NOT NULL DEFAULT 'pillar', status TEXT NOT NULL DEFAULT 'planned',
  created_at TEXT NOT NULL, UNIQUE(project, slug));
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(slug),
  cluster_id INTEGER REFERENCES clusters(id), parent_id INTEGER REFERENCES items(id),
  kind TEXT NOT NULL, channel TEXT NOT NULL, account TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '{}', media TEXT NOT NULL DEFAULT '[]',
  attributes TEXT NOT NULL DEFAULT '{}', link TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planned', scheduled_at TEXT,
  approved_hash TEXT, approved_by TEXT, approved_at TEXT, review_notes TEXT NOT NULL DEFAULT '',
  gaps TEXT NOT NULL DEFAULT '[]', audit TEXT NOT NULL DEFAULT '{}',
  created_by TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
  claimed_by TEXT, claimed_at TEXT, external_ref TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS items_project_status ON items(project, status);
CREATE UNIQUE INDEX IF NOT EXISTS items_external_ref ON items(project, external_ref) WHERE external_ref IS NOT NULL;
CREATE TABLE IF NOT EXISTS revisions (
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id), n INTEGER NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, author TEXT NOT NULL, role TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claims (
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id), text TEXT NOT NULL,
  citation TEXT NOT NULL DEFAULT '', citation_url TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'unverified', verified_by TEXT, verified_at TEXT,
  notes TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, item_id INTEGER REFERENCES items(id), project TEXT, actor TEXT NOT NULL,
  role TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}', at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id), channel TEXT NOT NULL,
  account TEXT NOT NULL DEFAULT '', post_url TEXT NOT NULL DEFAULT '', post_id TEXT NOT NULL DEFAULT '',
  posted_at TEXT NOT NULL, posted_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
  id INTEGER PRIMARY KEY, publication_id INTEGER NOT NULL REFERENCES publications(id),
  measured_at TEXT NOT NULL, age_hours REAL NOT NULL, data TEXT NOT NULL, source TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS conversions (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, item_id INTEGER NOT NULL REFERENCES items(id),
  source TEXT NOT NULL, day TEXT NOT NULL, sessions REAL NOT NULL DEFAULT 0, signups REAL NOT NULL DEFAULT 0,
  revenue_usd REAL NOT NULL DEFAULT 0, UNIQUE(item_id, source, day));
CREATE TABLE IF NOT EXISTS costs (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, item_id INTEGER REFERENCES items(id),
  category TEXT NOT NULL, vendor TEXT NOT NULL DEFAULT '', amount_usd REAL NOT NULL,
  note TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, source TEXT NOT NULL, channel TEXT NOT NULL DEFAULT '',
  topic TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '{}',
  score REAL NOT NULL DEFAULT 0, observed_at TEXT NOT NULL, expires_at TEXT,
  status TEXT NOT NULL DEFAULT 'new', cluster_id INTEGER REFERENCES clusters(id), actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS learnings (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, statement TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '{}',
  evidence TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'proposed', proposed_by TEXT NOT NULL,
  decided_by TEXT, created_at TEXT NOT NULL, decided_at TEXT);
CREATE TABLE IF NOT EXISTS tokens (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, project TEXT,
  token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, sealed TEXT NOT NULL,
  hint TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project, name));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id), project TEXT NOT NULL, kind TEXT NOT NULL,
  status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL,
  created_at TEXT NOT NULL, finished_at TEXT);
"""
# Columns added after the first release: (table, column, definition)
MIGRATIONS = [
    ("tokens", "last_used_at", "TEXT"),
    ("tokens", "expires_at", "TEXT"),
    ("tokens", "created_by", "TEXT NOT NULL DEFAULT ''"),
]

JSON_COLUMNS = {
    "brand", "settings", "keywords", "body", "media", "attributes", "gaps", "audit",
    "detail", "data", "evidence", "scope", "result",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Store:
    """Thin wrapper: rows come back as dicts with JSON columns decoded."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        for table, column, definition in MIGRATIONS:
            if column not in {row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")}:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        for key in out.keys() & JSON_COLUMNS:
            if isinstance(out[key], str):
                out[key] = json.loads(out[key])
        return out

    @staticmethod
    def _encode(values: dict[str, Any]) -> dict[str, Any]:
        return {
            key: json.dumps(value, ensure_ascii=False, sort_keys=True) if key in JSON_COLUMNS else value
            for key, value in values.items()
        }

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [self._decode(row) for row in self._conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def insert(self, table: str, values: dict[str, Any]) -> int:
        encoded = self._encode(values)
        columns = ", ".join(encoded)
        marks = ", ".join("?" for _ in encoded)
        with self._lock:
            cursor = self._conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(encoded.values()))
            return int(cursor.lastrowid or 0)

    def update(self, table: str, key: str, key_value: Any, values: dict[str, Any], *, where: str = "",
               where_params: Iterable[Any] = ()) -> int:
        """Returns the number of rows changed, so callers can do compare-and-set with `where`."""
        encoded = self._encode(values)
        assignments = ", ".join(f"{column} = ?" for column in encoded)
        sql = f"UPDATE {table} SET {assignments} WHERE {key} = ?" + (f" AND {where}" if where else "")
        with self._lock:
            cursor = self._conn.execute(sql, (*encoded.values(), key_value, *where_params))
            return cursor.rowcount

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
