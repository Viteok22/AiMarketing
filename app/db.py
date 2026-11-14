from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    products TEXT NOT NULL DEFAULT '',
    audience TEXT NOT NULL DEFAULT '',
    advantages TEXT NOT NULL DEFAULT '',
    tone TEXT NOT NULL DEFAULT '',
    forbidden TEXT NOT NULL DEFAULT '',
    knowledge TEXT NOT NULL DEFAULT '',
    channel_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    request TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'drafting',
    post_text TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    revision_comment TEXT NOT NULL DEFAULT '',
    published_message_id INTEGER,
    published_chat_id INTEGER,
    selected_text_id TEXT,
    selected_image_id TEXT,
    scheduled_at TEXT,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS variants (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    batch_no INTEGER NOT NULL DEFAULT 1,
    index_no INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    image_prompt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_company (
    user_id INTEGER PRIMARY KEY,
    company_id TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS task_ui (
    task_id TEXT PRIMARY KEY,
    text_message_id INTEGER,
    photo_message_id INTEGER,
    control_message_id INTEGER,
    text_variant_id TEXT,
    image_variant_id TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS publications (
    task_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    chat_id INTEGER,
    message_id INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS reaction_state (
    task_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, user_id, emoji)
);

CREATE TABLE IF NOT EXISTS reaction_counts (
    task_id TEXT NOT NULL,
    emoji TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, emoji)
);

CREATE TABLE IF NOT EXISTS trend_sources (
    channel TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trend_posts (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    views INTEGER NOT NULL DEFAULT 0,
    reactions_json TEXT NOT NULL DEFAULT '{}',
    score INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL DEFAULT '',
    posted_at TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trend_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT,
    summary TEXT NOT NULL,
    ideas_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    company_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    interval_minutes INTEGER NOT NULL DEFAULT 180,
    prompt TEXT NOT NULL DEFAULT '',
    user_id INTEGER,
    next_run_at TEXT,
    last_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS post_metrics (
    task_id TEXT PRIMARY KEY,
    views INTEGER NOT NULL DEFAULT 0,
    reactions_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS channel_posts (
    company_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    views INTEGER NOT NULL DEFAULT 0,
    reactions_json TEXT NOT NULL DEFAULT '{}',
    posted_at TEXT,
    url TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (company_id, message_id),
    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analytics_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id TEXT,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_entries (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    planned_at TEXT NOT NULL,
    topic TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    task_id TEXT,
    user_id INTEGER,
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS style_examples (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    text TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_company ON tasks(company_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_variants_task ON variants(task_id, batch_no, kind);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id);
CREATE INDEX IF NOT EXISTS idx_publications_message ON publications(chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_trend_posts_channel ON trend_posts(channel, score);
CREATE INDEX IF NOT EXISTS idx_channel_posts_company ON channel_posts(company_id, message_id);
CREATE INDEX IF NOT EXISTS idx_calendar_company ON calendar_entries(company_id, planned_at);
CREATE INDEX IF NOT EXISTS idx_style_examples_company ON style_examples(company_id, created_at);
"""


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        task_columns = {
            row["name"] for row in self.fetchall("PRAGMA table_info(tasks)")
        }
        if "scheduled_at" not in task_columns:
            self.execute("ALTER TABLE tasks ADD COLUMN scheduled_at TEXT")
        insight_columns = {
            row["name"] for row in self.fetchall("PRAGMA table_info(trend_insights)")
        }
        if "company_id" not in insight_columns:
            self.execute("ALTER TABLE trend_insights ADD COLUMN company_id TEXT")
            first = self.fetchone("SELECT id FROM companies ORDER BY created_at LIMIT 1")
            if first:
                self.execute(
                    "UPDATE trend_insights SET company_id=? WHERE company_id IS NULL",
                    (first["id"],),
                )

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, tuple(params))
            self._conn.commit()
            return cursor

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.executemany(sql, [tuple(row) for row in rows])
            self._conn.commit()
            return cursor

    def fetchone(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchone()

    def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()
