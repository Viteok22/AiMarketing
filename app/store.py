from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config import relative_path, settings
from app.db import Database
from app.models import ChatMessage, Company, Task, Variant, utcnow

# Публичные каналы для первичного анализа трендов.
# Список можно менять в админке; недоступные каналы просто пропускаются.
DEFAULT_TREND_SOURCES = [
    "arkhlikbez",
    "russiabuild",
    "ggreat_of_development",
    "moskvadetaly",
    "commers_estate",
    "promdesing",
    "lartaglass",
]

POSITIVE_REACTIONS = {"👍", "❤", "🔥", "🎉", "👏", "💯", "😍", "🙌", "✅"}
NEGATIVE_REACTIONS = {"👎", "😢", "😡", "🤬", "💔", "😞", "😔"}


def _iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def _dt(value: str | None) -> datetime:
    if not value:
        return utcnow().astimezone()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return utcnow().astimezone()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def _emoji_key(value: str) -> str:
    return (value or "").replace("\ufe0f", "").strip()


class Store:
    """SQLite-хранилище компаний, задач, вариантов, истории, реакций и трендов."""

    def __init__(self, db_path: Path | str | None = None, seed_path: Path | None = None) -> None:
        self.db = Database(db_path or settings.database_path)
        self.seed_path = seed_path or settings.base_dir / "data" / "companies.json"
        self._ensure_seed()

    # ------------------------------------------------------------------ seed
    def _ensure_seed(self) -> None:
        row = self.db.fetchone("SELECT COUNT(*) AS count FROM companies")
        if row and row["count"] == 0 and Path(self.seed_path).exists():
            data = json.loads(Path(self.seed_path).read_text(encoding="utf-8"))
            for item in data.get("companies", []):
                self.db.execute(
                    """
                    INSERT INTO companies
                    (id, name, description, products, audience, advantages, tone,
                     forbidden, knowledge, channel_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.get("id") or f"c-{secrets.token_hex(3)}",
                        item.get("name", "Без названия"),
                        item.get("description", ""),
                        item.get("products", ""),
                        item.get("audience", ""),
                        item.get("advantages", ""),
                        item.get("tone", ""),
                        item.get("forbidden", ""),
                        item.get("knowledge", ""),
                        item.get("channel_id", ""),
                        _iso(),
                    ),
                )
        trend_count = self.db.fetchone("SELECT COUNT(*) AS count FROM trend_sources")
        if trend_count and trend_count["count"] == 0:
            for channel in DEFAULT_TREND_SOURCES:
                self.add_trend_source(channel)

    def recover_interrupted_tasks(self) -> None:
        """Вызывается один раз при старте приложения, не при каждом Store()."""
        self.db.execute(
            "UPDATE tasks SET status = 'failed', error = ?, updated_at = ? WHERE status = 'drafting'",
            ("Перезапуск во время генерации. Нажмите «Повторить».", _iso()),
        )
        self.db.execute(
            "UPDATE tasks SET status = 'failed', error = ?, updated_at = ? WHERE status = 'publishing'",
            (
                "Перезапуск во время публикации. Проверьте канал: возможно, пост уже опубликован.",
                _iso(),
            ),
        )
        self.db.execute(
            "UPDATE calendar_entries SET status = 'failed', error = ?, updated_at = ? "
            "WHERE status = 'generating'",
            (
                "Перезапуск во время генерации. Нажмите «Сгенерировать» ещё раз.",
                _iso(),
            ),
        )

    # ------------------------------------------------------------------ views
    @property
    def companies(self) -> dict[str, Company]:
        return {company.id: company for company in self.list_companies()}

    @property
    def tasks(self) -> dict[str, Task]:
        return {task.id: task for task in self.list_tasks(limit=10000)}

    # -------------------------------------------------------------- companies
    def create_company(self, data: dict) -> Company:
        company = Company(
            id=f"c-{secrets.token_hex(3)}",
            name=(data.get("name") or "Без названия").strip(),
            description=(data.get("description") or "").strip(),
            products=(data.get("products") or "").strip(),
            audience=(data.get("audience") or "").strip(),
            advantages=(data.get("advantages") or "").strip(),
            tone=(data.get("tone") or "").strip(),
            forbidden=(data.get("forbidden") or "").strip(),
            knowledge=(data.get("knowledge") or "").strip(),
            channel_id=(data.get("channel_id") or settings.telegram_channel_id).strip(),
        )
        self.db.execute(
            """
            INSERT INTO companies
            (id, name, description, products, audience, advantages, tone,
             forbidden, knowledge, channel_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company.id,
                company.name,
                company.description,
                company.products,
                company.audience,
                company.advantages,
                company.tone,
                company.forbidden,
                company.knowledge,
                company.channel_id,
                _iso(company.created_at),
            ),
        )
        return company

    def update_company(self, company_id: str, data: dict) -> Optional[Company]:
        company = self.get_company(company_id)
        if company is None:
            return None
        fields = (
            "name",
            "description",
            "products",
            "audience",
            "advantages",
            "tone",
            "forbidden",
            "knowledge",
            "channel_id",
        )
        values = {}
        for field in fields:
            values[field] = (data.get(field) if field in data else getattr(company, field)) or ""
            values[field] = values[field].strip()
        self.db.execute(
            """
            UPDATE companies SET name=?, description=?, products=?, audience=?,
                advantages=?, tone=?, forbidden=?, knowledge=?, channel_id=?
            WHERE id=?
            """,
            (
                values["name"],
                values["description"],
                values["products"],
                values["audience"],
                values["advantages"],
                values["tone"],
                values["forbidden"],
                values["knowledge"],
                values["channel_id"],
                company_id,
            ),
        )
        return self.get_company(company_id)

    def get_company(self, company_id: str) -> Optional[Company]:
        row = self.db.fetchone("SELECT * FROM companies WHERE id = ?", (company_id,))
        if row is None:
            return None
        return self._company_from_row(row)

    def list_companies(self) -> list[Company]:
        rows = self.db.fetchall("SELECT * FROM companies ORDER BY created_at ASC")
        return [self._company_from_row(row) for row in rows]

    def get_default_company(self) -> Optional[Company]:
        row = self.db.fetchone("SELECT * FROM companies ORDER BY created_at ASC LIMIT 1")
        return self._company_from_row(row) if row else None

    def bind_user(self, user_id: int, company_id: str) -> bool:
        if self.get_company(company_id) is None:
            return False
        self.db.execute(
            "INSERT INTO user_company (user_id, company_id) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET company_id = excluded.company_id",
            (user_id, company_id),
        )
        return True

    def get_company_for_user(self, user_id: int) -> Optional[Company]:
        row = self.db.fetchone(
            "SELECT company_id FROM user_company WHERE user_id = ?", (user_id,)
        )
        if row:
            return self.get_company(row["company_id"])
        return None

    # ------------------------------------------------------------------ tasks
    def create_task(self, company_id: str, user_id: int, request: str) -> Task:
        now = utcnow()
        task = Task(
            id=f"t-{secrets.token_hex(4)}",
            company_id=company_id,
            user_id=user_id,
            request=request.strip(),
            created_at=now,
            updated_at=now,
        )
        self.db.execute(
            """
            INSERT INTO tasks
            (id, company_id, user_id, request, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.company_id,
                task.user_id,
                task.request,
                task.status,
                _iso(now),
                _iso(now),
            ),
        )
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        row = self.db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self._task_from_row(row) if row else None

    def update_task(self, task_id: str, **changes) -> Optional[Task]:
        allowed = {
            "status",
            "post_text",
            "image_path",
            "revision_comment",
            "published_message_id",
            "published_chat_id",
            "selected_text_id",
            "selected_image_id",
            "scheduled_at",
            "error",
        }
        fields = {key: value for key, value in changes.items() if key in allowed}
        if not fields:
            return self.get_task(task_id)
        if fields.get("image_path"):
            fields["image_path"] = relative_path(fields["image_path"])
        if fields.get("scheduled_at") is not None:
            value = fields["scheduled_at"]
            if isinstance(value, datetime):
                fields["scheduled_at"] = value.astimezone(timezone.utc).isoformat()
            else:
                fields["scheduled_at"] = str(value)
        fields["updated_at"] = _iso()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.db.execute(
            f"UPDATE tasks SET {assignments} WHERE id = ?",
            (*fields.values(), task_id),
        )
        status = fields.get("status")
        calendar_status = {
            "published": "published",
            "rejected": "cancelled",
            "failed": "failed",
        }.get(status or "")
        if calendar_status:
            self.db.execute(
                "UPDATE calendar_entries SET status = ?, updated_at = ? "
                "WHERE task_id = ?",
                (calendar_status, _iso(), task_id),
            )
        return self.get_task(task_id)

    def list_tasks(self, company_id: Optional[str] = None, limit: int = 30) -> list[Task]:
        if company_id:
            rows = self.db.fetchall(
                "SELECT * FROM tasks WHERE company_id = ? ORDER BY created_at DESC LIMIT ?",
                (company_id, limit),
            )
        else:
            rows = self.db.fetchall(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        return [self._task_from_row(row) for row in rows]

    def get_last_task(self, user_id: int) -> Optional[Task]:
        row = self.db.fetchone(
            "SELECT * FROM tasks WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        return self._task_from_row(row) if row else None

    def get_pending_task(self, user_id: int) -> Optional[Task]:
        row = self.db.fetchone(
            "SELECT * FROM tasks WHERE user_id = ? AND status = 'pending_approval' "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        return self._task_from_row(row) if row else None

    def list_due_scheduled_tasks(self, now: datetime | None = None) -> list[Task]:
        moment = (now or utcnow()).astimezone(timezone.utc).isoformat()
        rows = self.db.fetchall(
            "SELECT * FROM tasks WHERE status = 'scheduled' "
            "AND scheduled_at IS NOT NULL AND scheduled_at <= ? "
            "ORDER BY scheduled_at ASC",
            (moment,),
        )
        return [self._task_from_row(row) for row in rows]

    def list_scheduled_tasks(
        self, user_id: int | None = None, company_id: str | None = None
    ) -> list[Task]:
        query = "SELECT * FROM tasks WHERE status = 'scheduled'"
        params: list = []
        if user_id is not None:
            query += " AND user_id = ?"
            params.append(user_id)
        if company_id:
            query += " AND company_id = ?"
            params.append(company_id)
        query += " ORDER BY scheduled_at ASC"
        rows = self.db.fetchall(query, tuple(params))
        return [self._task_from_row(row) for row in rows]

    # --------------------------------------------------------------- schedules
    def get_schedule(self, company_id: str) -> Optional[dict]:
        row = self.db.fetchone(
            "SELECT * FROM schedules WHERE company_id = ?", (company_id,)
        )
        return dict(row) if row else None

    def upsert_schedule(
        self,
        company_id: str,
        enabled: bool,
        interval_minutes: int,
        prompt: str,
        user_id: int | None = None,
    ) -> dict:
        interval = max(1, int(interval_minutes))
        now = utcnow()
        next_run = (
            (now + timedelta(minutes=interval)).astimezone(timezone.utc).isoformat()
            if enabled
            else None
        )
        existing = self.get_schedule(company_id)
        if existing:
            self.db.execute(
                "UPDATE schedules SET enabled=?, interval_minutes=?, prompt=?, "
                "user_id=?, next_run_at=?, updated_at=? WHERE company_id=?",
                (
                    1 if enabled else 0,
                    interval,
                    prompt,
                    user_id,
                    next_run,
                    _iso(now),
                    company_id,
                ),
            )
        else:
            self.db.execute(
                "INSERT INTO schedules "
                "(company_id, enabled, interval_minutes, prompt, user_id, "
                "next_run_at, last_run_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    company_id,
                    1 if enabled else 0,
                    interval,
                    prompt,
                    user_id,
                    next_run,
                    _iso(now),
                    _iso(now),
                ),
            )
        return self.get_schedule(company_id) or {}

    def list_due_schedules(self, now: datetime | None = None) -> list[dict]:
        moment = (now or utcnow()).astimezone(timezone.utc).isoformat()
        rows = self.db.fetchall(
            "SELECT s.*, c.name AS company_name FROM schedules s "
            "JOIN companies c ON c.id = s.company_id "
            "WHERE s.enabled = 1 AND s.next_run_at IS NOT NULL "
            "AND s.next_run_at <= ? ORDER BY s.next_run_at ASC",
            (moment,),
        )
        return [dict(row) for row in rows]

    def mark_schedule_run(self, company_id: str, interval_minutes: int) -> None:
        now = utcnow()
        next_run = (now + timedelta(minutes=max(1, int(interval_minutes)))).astimezone(
            timezone.utc
        )
        self.db.execute(
            "UPDATE schedules SET last_run_at=?, next_run_at=?, updated_at=? "
            "WHERE company_id=?",
            (
                _iso(now),
                next_run.isoformat(),
                _iso(now),
                company_id,
            ),
        )

    # --------------------------------------------------------------- calendar
    def create_calendar_entry(
        self,
        company_id: str,
        planned_at: datetime,
        topic: str,
        user_id: int | None = None,
    ) -> dict:
        if planned_at.tzinfo is None:
            planned_at = planned_at.astimezone()
        now = utcnow()
        entry_id = f"cal-{secrets.token_hex(4)}"
        self.db.execute(
            """
            INSERT INTO calendar_entries
            (id, company_id, planned_at, topic, status, task_id, user_id,
             error, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'planned', NULL, ?, '', ?, ?)
            """,
            (
                entry_id,
                company_id,
                planned_at.astimezone(timezone.utc).isoformat(),
                topic.strip(),
                user_id,
                _iso(now),
                _iso(now),
            ),
        )
        return self.get_calendar_entry(entry_id) or {}

    def get_calendar_entry(self, entry_id: str) -> Optional[dict]:
        row = self.db.fetchone(
            "SELECT * FROM calendar_entries WHERE id = ?", (entry_id,)
        )
        return self._calendar_from_row(row) if row else None

    def list_calendar_entries(
        self,
        company_id: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM calendar_entries WHERE company_id = ?"
        params: list = [company_id]
        if start is not None:
            query += " AND planned_at >= ?"
            params.append(start.astimezone(timezone.utc).isoformat())
        if end is not None:
            query += " AND planned_at < ?"
            params.append(end.astimezone(timezone.utc).isoformat())
        query += " ORDER BY planned_at ASC"
        rows = self.db.fetchall(query, tuple(params))
        return [self._calendar_from_row(row) for row in rows]

    def list_due_calendar_entries(self, now: datetime | None = None) -> list[dict]:
        moment = (now or utcnow()).astimezone(timezone.utc).isoformat()
        rows = self.db.fetchall(
            "SELECT * FROM calendar_entries WHERE status = 'planned' "
            "AND planned_at <= ? ORDER BY planned_at ASC",
            (moment,),
        )
        return [self._calendar_from_row(row) for row in rows]

    def update_calendar_entry(self, entry_id: str, **fields) -> Optional[dict]:
        allowed = {"status", "task_id", "user_id", "error", "topic", "planned_at"}
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return self.get_calendar_entry(entry_id)
        if "planned_at" in values:
            planned_at = values["planned_at"]
            if isinstance(planned_at, datetime):
                if planned_at.tzinfo is None:
                    planned_at = planned_at.astimezone()
                values["planned_at"] = planned_at.astimezone(timezone.utc).isoformat()
            else:
                values["planned_at"] = str(planned_at)
        values["updated_at"] = _iso()
        assignments = ", ".join(f"{key} = ?" for key in values)
        self.db.execute(
            f"UPDATE calendar_entries SET {assignments} WHERE id = ?",
            (*values.values(), entry_id),
        )
        return self.get_calendar_entry(entry_id)

    def cancel_calendar_entry(self, entry_id: str, company_id: str) -> bool:
        entry = self.get_calendar_entry(entry_id)
        if entry is None or entry["company_id"] != company_id:
            return False
        if entry["status"] == "published":
            return False
        self.update_calendar_entry(entry_id, status="cancelled")
        return True

    def list_company_users(self, company_id: str) -> list[int]:
        rows = self.db.fetchall(
            "SELECT user_id FROM user_company WHERE company_id = ? ORDER BY user_id",
            (company_id,),
        )
        return [int(row["user_id"]) for row in rows]

    # ---------------------------------------------------------------- history
    def add_history(self, user_id: int, role: str, text: str) -> None:
        self.db.execute(
            "INSERT INTO history (user_id, role, text, created_at) VALUES (?, ?, ?, ?)",
            (user_id, role, text.strip(), _iso()),
        )
        self.db.execute(
            "DELETE FROM history WHERE user_id = ? AND id NOT IN "
            "(SELECT id FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 100)",
            (user_id, user_id),
        )

    def get_history_text(self, user_id: int, limit: int = 12) -> str:
        rows = self.db.fetchall(
            "SELECT role, text FROM history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        if not rows:
            return "История пуста."
        lines = []
        for row in reversed(rows):
            who = "Пользователь" if row["role"] == "user" else "AI-маркетолог"
            lines.append(f"{who}: {row['text']}")
        return "\n".join(lines)

    # --------------------------------------------------------------- variants
    def add_variant(
        self,
        task_id: str,
        kind: str,
        batch_no: int,
        index_no: int,
        content: str = "",
        image_path: str = "",
        image_prompt: str = "",
    ) -> Variant:
        variant = Variant(
            id=f"v-{secrets.token_hex(4)}",
            task_id=task_id,
            kind=kind,
            batch_no=batch_no,
            index_no=index_no,
            content=content,
            image_path=relative_path(image_path) if image_path else "",
            image_prompt=image_prompt,
        )
        self.db.execute(
            """
            INSERT INTO variants
            (id, task_id, kind, batch_no, index_no, content, image_path, image_prompt, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                variant.id,
                variant.task_id,
                variant.kind,
                variant.batch_no,
                variant.index_no,
                variant.content,
                variant.image_path,
                variant.image_prompt,
                _iso(variant.created_at),
            ),
        )
        return variant

    def list_variants(
        self, task_id: str, kind: Optional[str] = None, batch_no: Optional[int] = None
    ) -> list[Variant]:
        clauses = ["task_id = ?"]
        params: list = [task_id]
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if batch_no is not None:
            clauses.append("batch_no = ?")
            params.append(batch_no)
        rows = self.db.fetchall(
            f"SELECT * FROM variants WHERE {' AND '.join(clauses)} "
            "ORDER BY batch_no ASC, kind ASC, index_no ASC",
            params,
        )
        return [self._variant_from_row(row) for row in rows]

    def get_variant(self, variant_id: str) -> Optional[Variant]:
        row = self.db.fetchone("SELECT * FROM variants WHERE id = ?", (variant_id,))
        return self._variant_from_row(row) if row else None

    def update_variant(
        self,
        variant_id: str,
        content: str | None = None,
        image_prompt: str | None = None,
    ) -> Optional[Variant]:
        values = {}
        if content is not None:
            values["content"] = content
        if image_prompt is not None:
            values["image_prompt"] = image_prompt
        if not values:
            return self.get_variant(variant_id)
        assignments = ", ".join(f"{key} = ?" for key in values)
        self.db.execute(
            f"UPDATE variants SET {assignments} WHERE id = ?",
            (*values.values(), variant_id),
        )
        variant = self.get_variant(variant_id)
        if variant and variant.kind == "text":
            task = self.get_task(variant.task_id)
            if task and task.selected_text_id == variant.id:
                self.update_task(task.id, post_text=variant.content)
        return variant

    def latest_batch(self, task_id: str) -> int:
        row = self.db.fetchone(
            "SELECT MAX(batch_no) AS batch FROM variants WHERE task_id = ?", (task_id,)
        )
        return int(row["batch"] or 0) if row else 0

    def next_batch(self, task_id: str) -> int:
        return self.latest_batch(task_id) + 1

    def current_variants(self, task_id: str) -> list[Variant]:
        batch = self.latest_batch(task_id)
        if batch == 0:
            return []
        return self.list_variants(task_id, batch_no=batch)

    def count_variants(self, task_id: str, kind: str, batch_no: Optional[int] = None) -> int:
        if batch_no is None:
            batch_no = self.latest_batch(task_id)
        row = self.db.fetchone(
            "SELECT COUNT(*) AS count FROM variants WHERE task_id = ? AND kind = ? AND batch_no = ?",
            (task_id, kind, batch_no),
        )
        return int(row["count"]) if row else 0

    def select_variant(self, task_id: str, kind: str, variant_id: str) -> bool:
        variant = self.get_variant(variant_id)
        if variant is None or variant.task_id != task_id or variant.kind != kind:
            return False
        if kind == "text":
            self.update_task(task_id, selected_text_id=variant.id, post_text=variant.content)
        elif kind == "image":
            self.update_task(task_id, selected_image_id=variant.id, image_path=variant.image_path)
        else:
            return False
        return True

    # ---------------------------------------------------------------- task UI
    def save_task_ui(self, task_id: str, **fields) -> None:
        allowed = {
            "text_message_id",
            "photo_message_id",
            "control_message_id",
            "text_variant_id",
            "image_variant_id",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        existing = self.get_task_ui(task_id) or {}
        merged = {**existing, **values}
        self.db.execute(
            """
            INSERT INTO task_ui
            (task_id, text_message_id, photo_message_id, control_message_id,
             text_variant_id, image_variant_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                text_message_id = excluded.text_message_id,
                photo_message_id = excluded.photo_message_id,
                control_message_id = excluded.control_message_id,
                text_variant_id = excluded.text_variant_id,
                image_variant_id = excluded.image_variant_id,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                merged.get("text_message_id"),
                merged.get("photo_message_id"),
                merged.get("control_message_id"),
                merged.get("text_variant_id"),
                merged.get("image_variant_id"),
                _iso(),
            ),
        )

    def get_task_ui(self, task_id: str) -> Optional[dict]:
        row = self.db.fetchone("SELECT * FROM task_ui WHERE task_id = ?", (task_id,))
        return dict(row) if row else None

    def clear_task_ui(self, task_id: str) -> None:
        self.db.execute("DELETE FROM task_ui WHERE task_id = ?", (task_id,))

    # ----------------------------------------------------------- publications
    def save_publication(
        self, task_id: str, channel_id: str, chat_id: Optional[int], message_id: int
    ) -> None:
        self.db.execute(
            """
            INSERT INTO publications (task_id, channel_id, chat_id, message_id, published_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                chat_id = excluded.chat_id,
                message_id = excluded.message_id,
                published_at = excluded.published_at
            """,
            (task_id, channel_id, chat_id, message_id, _iso()),
        )

    def get_publication_by_message(self, chat_id: int, message_id: int) -> Optional[str]:
        row = self.db.fetchone(
            "SELECT task_id FROM publications WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        )
        return row["task_id"] if row else None

    def get_publication(self, task_id: str) -> Optional[dict]:
        row = self.db.fetchone("SELECT * FROM publications WHERE task_id = ?", (task_id,))
        return dict(row) if row else None

    def list_publications(
        self, limit: int = 50, company_id: Optional[str] = None
    ) -> list[dict]:
        if company_id:
            rows = self.db.fetchall(
                """
                SELECT p.*, t.request, t.post_text, t.user_id, c.name AS company_name,
                       COALESCE(m.views, 0) AS views,
                       m.reactions_json AS public_reactions_json,
                       m.updated_at AS metrics_updated_at
                FROM publications p
                JOIN tasks t ON t.id = p.task_id
                JOIN companies c ON c.id = t.company_id
                LEFT JOIN post_metrics m ON m.task_id = p.task_id
                WHERE t.company_id = ?
                ORDER BY p.published_at DESC LIMIT ?
                """,
                (company_id, limit),
            )
        else:
            rows = self.db.fetchall(
                """
                SELECT p.*, t.request, t.post_text, t.user_id, c.name AS company_name,
                       COALESCE(m.views, 0) AS views,
                       m.reactions_json AS public_reactions_json,
                       m.updated_at AS metrics_updated_at
                FROM publications p
                JOIN tasks t ON t.id = p.task_id
                JOIN companies c ON c.id = t.company_id
                LEFT JOIN post_metrics m ON m.task_id = p.task_id
                ORDER BY p.published_at DESC LIMIT ?
                """,
                (limit,),
            )
        return [dict(row) for row in rows]

    # -------------------------------------------------------------- reactions
    def save_user_reactions(self, task_id: str, user_id: int, emojis: list[str]) -> None:
        self.db.execute(
            "DELETE FROM reaction_state WHERE task_id = ? AND user_id = ?",
            (task_id, user_id),
        )
        for emoji in emojis:
            self.db.execute(
                "INSERT OR REPLACE INTO reaction_state (task_id, user_id, emoji, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (task_id, user_id, emoji, _iso()),
            )

    def save_reaction_counts(self, task_id: str, counts: dict[str, int]) -> None:
        self.db.execute("DELETE FROM reaction_counts WHERE task_id = ?", (task_id,))
        for emoji, count in counts.items():
            self.db.execute(
                "INSERT OR REPLACE INTO reaction_counts (task_id, emoji, count, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (task_id, emoji, int(count), _iso()),
            )

    def get_reaction_summary(self, task_id: str) -> dict:
        counts: dict[str, int] = {}
        rows = self.db.fetchall(
            "SELECT emoji, count FROM reaction_counts WHERE task_id = ?", (task_id,)
        )
        source = "counts"
        if not rows:
            metrics = self.get_post_metrics(task_id)
            public = (metrics or {}).get("reactions") or {}
            if public:
                source = "metrics"
                for emoji, count in public.items():
                    key = _emoji_key(emoji)
                    counts[key] = counts.get(key, 0) + int(count)
            else:
                source = "state"
                rows = self.db.fetchall(
                    "SELECT emoji, COUNT(*) AS count FROM reaction_state "
                    "WHERE task_id = ? GROUP BY emoji",
                    (task_id,),
                )
        for row in rows:
            emoji = _emoji_key(row["emoji"])
            counts[emoji] = counts.get(emoji, 0) + int(row["count"])
        positive = sum(value for emoji, value in counts.items() if emoji in POSITIVE_REACTIONS)
        negative = sum(value for emoji, value in counts.items() if emoji in NEGATIVE_REACTIONS)
        total = sum(counts.values())
        return {
            "source": source,
            "counts": dict(sorted(counts.items(), key=lambda item: -item[1])),
            "positive": positive,
            "negative": negative,
            "neutral": max(0, total - positive - negative),
            "total": total,
            "score": positive - negative,
        }

    def list_reaction_reports(
        self, limit: int = 10, company_id: Optional[str] = None
    ) -> list[dict]:
        reports = []
        for publication in self.list_publications(limit=limit, company_id=company_id):
            reports.append(
                {
                    **publication,
                    "reactions": self.get_reaction_summary(publication["task_id"]),
                }
            )
        return reports

    # ------------------------------------------------------------ post metrics
    def save_post_metrics(
        self, task_id: str, views: int, reactions: dict | None = None
    ) -> None:
        self.db.execute(
            """
            INSERT INTO post_metrics (task_id, views, reactions_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                views = excluded.views,
                reactions_json = excluded.reactions_json,
                updated_at = excluded.updated_at
            """,
            (
                task_id,
                int(views),
                json.dumps(reactions or {}, ensure_ascii=False),
                _iso(),
            ),
        )
        if reactions is not None:
            # t.me/s отдаёт актуальные реакции даже если Telegram не прислал
            # message_reaction: держим /stats и AI-вывод на одном источнике.
            self.save_reaction_counts(task_id, reactions)

    def get_post_metrics(self, task_id: str) -> Optional[dict]:
        row = self.db.fetchone("SELECT * FROM post_metrics WHERE task_id = ?", (task_id,))
        if row is None:
            return None
        data = dict(row)
        try:
            data["reactions"] = json.loads(data.get("reactions_json") or "{}")
        except json.JSONDecodeError:
            data["reactions"] = {}
        return data

    # ----------------------------------------------------------- channel posts
    def save_channel_posts(self, company_id: str, posts: list[dict]) -> None:
        for post in posts:
            self.db.execute(
                """
                INSERT INTO channel_posts
                (company_id, message_id, text, views, reactions_json, posted_at, url, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, message_id) DO UPDATE SET
                    text = excluded.text,
                    views = excluded.views,
                    reactions_json = excluded.reactions_json,
                    posted_at = excluded.posted_at,
                    url = excluded.url,
                    updated_at = excluded.updated_at
                """,
                (
                    company_id,
                    int(post["message_id"]),
                    (post.get("text") or "").strip(),
                    int(post.get("views") or 0),
                    json.dumps(post.get("reactions") or {}, ensure_ascii=False),
                    post.get("posted_at"),
                    post.get("url") or "",
                    _iso(),
                ),
            )
        self.db.execute(
            "DELETE FROM channel_posts WHERE company_id = ? AND message_id NOT IN "
            "(SELECT message_id FROM channel_posts WHERE company_id = ? "
            "ORDER BY message_id DESC LIMIT 300)",
            (company_id, company_id),
        )

    def list_channel_posts(self, company_id: str, limit: int = 20) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM channel_posts WHERE company_id = ? "
            "ORDER BY message_id DESC LIMIT ?",
            (company_id, limit),
        )
        result = []
        for row in rows:
            data = dict(row)
            try:
                data["reactions"] = json.loads(data.get("reactions_json") or "{}")
            except json.JSONDecodeError:
                data["reactions"] = {}
            result.append(data)
        return result

    # ---------------------------------------------------------- analytics AI
    def save_analytics_insight(self, company_id: str, summary: str) -> None:
        self.db.execute(
            "INSERT INTO analytics_insights (company_id, summary, created_at) "
            "VALUES (?, ?, ?)",
            (company_id, summary, _iso()),
        )
        self.db.execute(
            "DELETE FROM analytics_insights WHERE company_id = ? AND id NOT IN "
            "(SELECT id FROM analytics_insights WHERE company_id = ? "
            "ORDER BY id DESC LIMIT 10)",
            (company_id, company_id),
        )

    def get_latest_analytics_insight(self, company_id: str | None = None) -> Optional[dict]:
        if company_id:
            row = self.db.fetchone(
                "SELECT * FROM analytics_insights WHERE company_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (company_id,),
            )
        else:
            row = self.db.fetchone(
                "SELECT * FROM analytics_insights ORDER BY id DESC LIMIT 1"
            )
        return dict(row) if row else None

    # ----------------------------------------------------------------- trends
    def add_trend_source(self, channel: str) -> str:
        normalized = (channel or "").strip().lstrip("@").lower()
        if not normalized:
            return ""
        self.db.execute(
            "INSERT INTO trend_sources (channel, enabled, created_at) VALUES (?, 1, ?) "
            "ON CONFLICT(channel) DO UPDATE SET enabled = 1",
            (normalized, _iso()),
        )
        return normalized

    def list_trend_sources(self, enabled_only: bool = True) -> list[dict]:
        if enabled_only:
            rows = self.db.fetchall(
                "SELECT * FROM trend_sources WHERE enabled = 1 ORDER BY created_at ASC"
            )
        else:
            rows = self.db.fetchall("SELECT * FROM trend_sources ORDER BY created_at ASC")
        return [dict(row) for row in rows]

    def set_trend_source_enabled(self, channel: str, enabled: bool) -> None:
        self.db.execute(
            "UPDATE trend_sources SET enabled = ? WHERE channel = ?",
            (1 if enabled else 0, channel.lstrip("@").lower()),
        )

    def delete_trend_source(self, channel: str) -> None:
        normalized = channel.lstrip("@").lower()
        self.db.execute("DELETE FROM trend_sources WHERE channel = ?", (normalized,))
        self.db.execute("DELETE FROM trend_posts WHERE channel = ?", (normalized,))

    def save_trend_posts(self, posts: list[dict]) -> None:
        for post in posts:
            self.db.execute(
                """
                INSERT INTO trend_posts
                (id, channel, message_id, text, views, reactions_json, score, url, posted_at, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    text = excluded.text,
                    views = excluded.views,
                    reactions_json = excluded.reactions_json,
                    score = excluded.score,
                    url = excluded.url,
                    posted_at = excluded.posted_at,
                    fetched_at = excluded.fetched_at
                """,
                (
                    post["id"],
                    post["channel"],
                    post["message_id"],
                    post.get("text", ""),
                    int(post.get("views", 0)),
                    post.get("reactions_json", "{}"),
                    int(post.get("score", 0)),
                    post.get("url", ""),
                    post.get("posted_at"),
                    _iso(),
                ),
            )

    def list_trend_posts(self, channel: Optional[str] = None, limit: int = 50) -> list[dict]:
        if channel:
            rows = self.db.fetchall(
                "SELECT * FROM trend_posts WHERE channel = ? ORDER BY score DESC, fetched_at DESC LIMIT ?",
                (channel.lstrip("@").lower(), limit),
            )
        else:
            rows = self.db.fetchall(
                "SELECT * FROM trend_posts ORDER BY score DESC, fetched_at DESC LIMIT ?",
                (limit,),
            )
        return [dict(row) for row in rows]

    def save_trend_insight(
        self, summary: str, ideas: list[str], company_id: str | None = None
    ) -> None:
        self.db.execute(
            "INSERT INTO trend_insights (company_id, summary, ideas_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (company_id, summary, json.dumps(ideas, ensure_ascii=False), _iso()),
        )
        if company_id:
            self.db.execute(
                "DELETE FROM trend_insights WHERE company_id = ? AND id NOT IN "
                "(SELECT id FROM trend_insights WHERE company_id = ? "
                "ORDER BY id DESC LIMIT 20)",
                (company_id, company_id),
            )
        else:
            self.db.execute(
                "DELETE FROM trend_insights WHERE id NOT IN "
                "(SELECT id FROM trend_insights ORDER BY id DESC LIMIT 20)"
            )

    def get_latest_trend_insight(self, company_id: str | None = None) -> Optional[dict]:
        if company_id:
            row = self.db.fetchone(
                "SELECT * FROM trend_insights WHERE company_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (company_id,),
            )
        else:
            row = self.db.fetchone(
                "SELECT * FROM trend_insights ORDER BY id DESC LIMIT 1"
            )
        if row is None:
            return None
        data = dict(row)
        try:
            data["ideas"] = json.loads(data.get("ideas_json") or "[]")
        except json.JSONDecodeError:
            data["ideas"] = []
        return data

    # ------------------------------------------------------------------- misc
    def close(self) -> None:
        self.db.close()

    def reset(self) -> None:
        for table in (
            "reaction_state",
            "reaction_counts",
            "publications",
            "post_metrics",
            "channel_posts",
            "calendar_entries",
            "analytics_insights",
            "task_ui",
            "variants",
            "history",
            "user_company",
            "schedules",
            "trend_posts",
            "trend_insights",
            "trend_sources",
            "tasks",
            "companies",
        ):
            self.db.execute(f"DELETE FROM {table}")
        self._ensure_seed()

    # --------------------------------------------------------------- converters
    @staticmethod
    def _company_from_row(row) -> Company:
        return Company(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            products=row["products"] or "",
            audience=row["audience"] or "",
            advantages=row["advantages"] or "",
            tone=row["tone"] or "",
            forbidden=row["forbidden"] or "",
            knowledge=row["knowledge"] or "",
            channel_id=row["channel_id"] or "",
            created_at=_dt(row["created_at"]),
        )

    @staticmethod
    def _calendar_from_row(row) -> dict:
        data = dict(row)
        data["planned_at_dt"] = _dt(data["planned_at"])
        data["created_at_dt"] = _dt(data["created_at"])
        data["updated_at_dt"] = _dt(data["updated_at"])
        return data

    @staticmethod
    def _task_from_row(row) -> Task:
        return Task(
            id=row["id"],
            company_id=row["company_id"],
            user_id=row["user_id"],
            request=row["request"],
            status=row["status"],
            post_text=row["post_text"] or "",
            image_path=row["image_path"] or "",
            revision_comment=row["revision_comment"] or "",
            published_message_id=row["published_message_id"],
            published_chat_id=row["published_chat_id"],
            selected_text_id=row["selected_text_id"],
            selected_image_id=row["selected_image_id"],
            scheduled_at=_dt(row["scheduled_at"]) if row["scheduled_at"] else None,
            error=row["error"] or "",
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
        )

    @staticmethod
    def _variant_from_row(row) -> Variant:
        return Variant(
            id=row["id"],
            task_id=row["task_id"],
            kind=row["kind"],
            batch_no=row["batch_no"],
            index_no=row["index_no"],
            content=row["content"] or "",
            image_path=row["image_path"] or "",
            image_prompt=row["image_prompt"] or "",
            created_at=_dt(row["created_at"]),
        )
