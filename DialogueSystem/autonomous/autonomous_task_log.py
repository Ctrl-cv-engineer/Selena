"""SQLite-backed autonomous task persistence for DialogueSystem."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta


DATE_OUTPUT_FORMAT = "%Y-%m-%d"
DATETIME_OUTPUT_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_INPUT_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
)

TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_INTERRUPT_REQUESTED = "interrupt_requested"
TASK_STATUS_PAUSED = "paused"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CARRIED_OVER = "carried_over"
VALID_TASK_STATUSES = {
    TASK_STATUS_PENDING,
    TASK_STATUS_RUNNING,
    TASK_STATUS_INTERRUPT_REQUESTED,
    TASK_STATUS_PAUSED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CARRIED_OVER,
}
TASK_ACTIVE_STATUSES = {
    TASK_STATUS_RUNNING,
    TASK_STATUS_INTERRUPT_REQUESTED,
}
TASK_RUNNABLE_STATUSES = {
    TASK_STATUS_PENDING,
    TASK_STATUS_PAUSED,
}
TASK_TERMINAL_STATUSES = {
    TASK_STATUS_COMPLETED,
    TASK_STATUS_FAILED,
    TASK_STATUS_CARRIED_OVER,
}

ATTEMPT_STATUS_RUNNING = "running"
ATTEMPT_STATUS_INTERRUPT_REQUESTED = "interrupt_requested"
ATTEMPT_STATUS_CANCELLED = "cancelled"
ATTEMPT_STATUS_TIMED_OUT = "timed_out"
ATTEMPT_STATUS_FAILED = "failed"
ATTEMPT_STATUS_COMPLETED = "completed"
ATTEMPT_STATUS_STALE = "stale"
VALID_ATTEMPT_STATUSES = {
    ATTEMPT_STATUS_RUNNING,
    ATTEMPT_STATUS_INTERRUPT_REQUESTED,
    ATTEMPT_STATUS_CANCELLED,
    ATTEMPT_STATUS_TIMED_OUT,
    ATTEMPT_STATUS_FAILED,
    ATTEMPT_STATUS_COMPLETED,
    ATTEMPT_STATUS_STALE,
}
ATTEMPT_ACTIVE_STATUSES = {
    ATTEMPT_STATUS_RUNNING,
    ATTEMPT_STATUS_INTERRUPT_REQUESTED,
}
ATTEMPT_TERMINAL_STATUSES = VALID_ATTEMPT_STATUSES - ATTEMPT_ACTIVE_STATUSES

TASK_SOURCE_SELF_GENERATED = "self_generated"
TASK_SOURCE_CARRIED_OVER = "carried_over"
VALID_TASK_SOURCES = {
    TASK_SOURCE_SELF_GENERATED,
    TASK_SOURCE_CARRIED_OVER,
}

SESSION_FINISH_REASONS = {
    "",
    "all_completed",
    "partial",
    "no_tasks",
    "token_limit",
    "user_interrupt",
    "error",
    "interrupt_limit",
}

logger = logging.getLogger("autonomous_task_mode.storage")


class AutonomousTaskValidationError(ValueError):
    """Raised when autonomous task payloads are malformed."""


def _normalize_optional_input(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _normalize_required_text(value, field_name: str) -> str:
    normalized = _normalize_optional_input(value)
    if normalized is None:
        raise AutonomousTaskValidationError(f"{field_name} is required.")
    return str(normalized)


def normalize_task_date(value, *, field_name: str = "task_date") -> str:
    normalized = _normalize_optional_input(value)
    if normalized is None:
        raise AutonomousTaskValidationError(f"{field_name} is required.")

    if isinstance(normalized, datetime):
        return normalized.strftime(DATE_OUTPUT_FORMAT)
    if isinstance(normalized, date):
        return normalized.strftime(DATE_OUTPUT_FORMAT)

    text = str(normalized)
    for fmt in DATE_INPUT_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime(DATE_OUTPUT_FORMAT)
        except ValueError:
            continue

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AutonomousTaskValidationError(
            f"Invalid {field_name} '{text}'. Use YYYY-MM-DD or YYYY/MM/DD."
        ) from exc

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.strftime(DATE_OUTPUT_FORMAT)


def _normalize_status(value, *, valid_values: set[str], field_name: str) -> str:
    normalized = _normalize_optional_input(value)
    if normalized not in valid_values:
        raise AutonomousTaskValidationError(
            f"Invalid {field_name} '{value}'. Valid values: {sorted(valid_values)}."
        )
    return str(normalized)


def _normalize_non_negative_int(value, *, field_name: str) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise AutonomousTaskValidationError(f"{field_name} must be an integer.") from exc
    if normalized < 0:
        raise AutonomousTaskValidationError(f"{field_name} must be >= 0.")
    return normalized


def _parse_timestamp(value) -> datetime | None:
    text = str(_normalize_optional_input(value) or "")
    if not text:
        return None
    for fmt in (DATETIME_OUTPUT_FORMAT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _now_text() -> str:
    return _format_timestamp(datetime.now())


def _format_timestamp(value: datetime) -> str:
    return value.strftime(DATETIME_OUTPUT_FORMAT)


def _preview_log_text(value, *, limit: int = 160) -> str:
    normalized_text = " ".join(str(value or "").strip().split())
    if len(normalized_text) <= limit:
        return normalized_text
    return f"{normalized_text[: max(0, limit - 3)]}..."


class AutonomousTaskLog:
    """Stores autonomous tasks, attempts, and daily session counters in SQLite."""

    def __init__(self, db_path: str):
        self.db_path = os.path.abspath(db_path)
        self._lock = threading.RLock()
        directory = os.path.dirname(self.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.initialize()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self):
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL;")
            connection.execute("PRAGMA foreign_keys=ON;")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_date TEXT NOT NULL,
                    task_content TEXT NOT NULL,
                    expected_goal TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    source TEXT NOT NULL DEFAULT 'self_generated',
                    current_attempt_id TEXT NOT NULL DEFAULT '',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    token_usage_input INTEGER NOT NULL DEFAULT 0,
                    token_usage_output INTEGER NOT NULL DEFAULT 0,
                    execution_log TEXT NOT NULL DEFAULT '',
                    resume_snapshot TEXT NOT NULL DEFAULT '',
                    pause_reason TEXT NOT NULL DEFAULT '',
                    carry_over_from_date TEXT,
                    carry_over_from_id INTEGER,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    pause_requested_at TEXT,
                    paused_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_autonomous_tasks_date
                ON autonomous_tasks (task_date, status)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_task_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    task_date TEXT NOT NULL,
                    lease_id TEXT NOT NULL,
                    subagent_task_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'running',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    result_summary TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    last_heartbeat_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES autonomous_tasks(id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_autonomous_task_attempts_task
                ON autonomous_task_attempts (task_id, status)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_autonomous_task_attempts_lease
                ON autonomous_task_attempts (lease_id)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_daily_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_date TEXT NOT NULL UNIQUE,
                    total_input_tokens INTEGER NOT NULL DEFAULT 0,
                    total_output_tokens INTEGER NOT NULL DEFAULT 0,
                    tasks_planned INTEGER NOT NULL DEFAULT 0,
                    tasks_completed INTEGER NOT NULL DEFAULT 0,
                    tasks_carried_over INTEGER NOT NULL DEFAULT 0,
                    interrupt_count INTEGER NOT NULL DEFAULT 0,
                    plan_generated_at TEXT,
                    session_finished_at TEXT,
                    finish_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS autonomous_session_leases (
                    session_date TEXT PRIMARY KEY,
                    lease_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    last_heartbeat_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    released_at TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_autonomous_session_leases_expires
                ON autonomous_session_leases (expires_at)
                """
            )
            connection.commit()
        logger.info("Autonomous task storage ready | db_path=%s", self.db_path)

    @staticmethod
    def _serialize_task_row(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "task_date": row["task_date"],
            "task_content": row["task_content"],
            "expected_goal": row["expected_goal"],
            "status": row["status"],
            "source": row["source"],
            "current_attempt_id": row["current_attempt_id"],
            "attempt_count": row["attempt_count"],
            "token_usage_input": row["token_usage_input"],
            "token_usage_output": row["token_usage_output"],
            "execution_log": row["execution_log"],
            "resume_snapshot": row["resume_snapshot"],
            "pause_reason": row["pause_reason"],
            "carry_over_from_date": row["carry_over_from_date"],
            "carry_over_from_id": row["carry_over_from_id"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "pause_requested_at": row["pause_requested_at"],
            "paused_at": row["paused_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _serialize_attempt_row(row):
        if row is None:
            return None
        return {
            "attempt_id": row["attempt_id"],
            "task_id": row["task_id"],
            "task_date": row["task_date"],
            "lease_id": row["lease_id"],
            "subagent_task_id": row["subagent_task_id"],
            "status": row["status"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "result_summary": row["result_summary"],
            "error_message": row["error_message"],
            "started_at": row["started_at"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "finished_at": row["finished_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _serialize_session_row(row):
        if row is None:
            return None
        return {
            "id": row["id"],
            "session_date": row["session_date"],
            "total_input_tokens": row["total_input_tokens"],
            "total_output_tokens": row["total_output_tokens"],
            "tasks_planned": row["tasks_planned"],
            "tasks_completed": row["tasks_completed"],
            "tasks_carried_over": row["tasks_carried_over"],
            "interrupt_count": row["interrupt_count"],
            "plan_generated_at": row["plan_generated_at"],
            "session_finished_at": row["session_finished_at"],
            "finish_reason": row["finish_reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _get_task_locked(self, connection, task_id: int):
        row = connection.execute(
            "SELECT * FROM autonomous_tasks WHERE id = ?",
            (int(task_id),),
        ).fetchone()
        return self._serialize_task_row(row)

    def _get_attempt_locked(self, connection, attempt_id: str):
        normalized_attempt_id = str(_normalize_optional_input(attempt_id) or "")
        if not normalized_attempt_id:
            return None
        row = connection.execute(
            "SELECT * FROM autonomous_task_attempts WHERE attempt_id = ?",
            (normalized_attempt_id,),
        ).fetchone()
        return self._serialize_attempt_row(row)

    def _get_session_locked(self, connection, session_date: str):
        row = connection.execute(
            "SELECT * FROM autonomous_daily_sessions WHERE session_date = ?",
            (session_date,),
        ).fetchone()
        return self._serialize_session_row(row)

    @staticmethod
    def _serialize_session_lease_row(row):
        if row is None:
            return None
        return {
            "session_date": row["session_date"],
            "lease_id": row["lease_id"],
            "owner_id": row["owner_id"],
            "acquired_at": row["acquired_at"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "expires_at": row["expires_at"],
            "released_at": row["released_at"],
            "updated_at": row["updated_at"],
        }

    def _get_session_lease_locked(self, connection, session_date: str):
        row = connection.execute(
            "SELECT * FROM autonomous_session_leases WHERE session_date = ?",
            (session_date,),
        ).fetchone()
        return self._serialize_session_lease_row(row)

    @staticmethod
    def _is_session_lease_expired(lease: dict | None, *, now: datetime | None = None) -> bool:
        if lease is None:
            return True
        if _normalize_optional_input(lease.get("released_at")):
            return True
        expires_at = _parse_timestamp(lease.get("expires_at"))
        if expires_at is None:
            return True
        return expires_at <= (now or datetime.now())

    def _get_or_create_daily_session_locked(self, connection, session_date: str, *, current_time: str):
        connection.execute(
            """
            INSERT OR IGNORE INTO autonomous_daily_sessions (
                session_date,
                created_at,
                updated_at
            ) VALUES (?, ?, ?)
            """,
            (session_date, current_time, current_time),
        )
        row = connection.execute(
            "SELECT * FROM autonomous_daily_sessions WHERE session_date = ?",
            (session_date,),
        ).fetchone()
        return self._serialize_session_row(row)

    def get_task(self, task_id: int):
        with self._lock, self._connect() as connection:
            return self._get_task_locked(connection, task_id)

    def get_attempt(self, attempt_id: str):
        with self._lock, self._connect() as connection:
            return self._get_attempt_locked(connection, attempt_id)

    def get_current_attempt(self, current_attempt_id: str):
        return self.get_attempt(current_attempt_id)

    def get_or_create_daily_session(self, session_date: str):
        normalized_date = normalize_task_date(session_date, field_name="session_date")
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            session = self._get_or_create_daily_session_locked(
                connection,
                normalized_date,
                current_time=current_time,
            )
            connection.commit()
        return session

    def acquire_session_lease(
        self,
        session_date: str,
        *,
        owner_id: str,
        ttl_seconds: int = 180,
    ):
        normalized_date = normalize_task_date(session_date, field_name="session_date")
        normalized_owner_id = _normalize_required_text(owner_id, "owner_id")
        normalized_ttl_seconds = max(
            30,
            _normalize_non_negative_int(ttl_seconds, field_name="ttl_seconds"),
        )
        now_dt = datetime.now()
        current_time = _format_timestamp(now_dt)
        expires_at = _format_timestamp(
            now_dt + timedelta(seconds=normalized_ttl_seconds)
        )
        next_lease_id = uuid.uuid4().hex
        previous_lease = None
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous_lease = self._get_session_lease_locked(connection, normalized_date)
            if previous_lease and not self._is_session_lease_expired(previous_lease, now=now_dt):
                connection.commit()
                logger.info(
                    "Autonomous session lease busy | session_date=%s | lease_id=%s | owner_id=%s | expires_at=%s",
                    normalized_date,
                    previous_lease.get("lease_id") or "",
                    previous_lease.get("owner_id") or "",
                    previous_lease.get("expires_at") or "",
                )
                return None
            connection.execute(
                """
                INSERT INTO autonomous_session_leases (
                    session_date,
                    lease_id,
                    owner_id,
                    acquired_at,
                    last_heartbeat_at,
                    expires_at,
                    released_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(session_date) DO UPDATE SET
                    lease_id = excluded.lease_id,
                    owner_id = excluded.owner_id,
                    acquired_at = excluded.acquired_at,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    expires_at = excluded.expires_at,
                    released_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_date,
                    next_lease_id,
                    normalized_owner_id,
                    current_time,
                    current_time,
                    expires_at,
                    current_time,
                ),
            )
            connection.commit()
            lease = self._get_session_lease_locked(connection, normalized_date)
        logger.info(
            "Autonomous session lease acquired | session_date=%s | lease_id=%s | owner_id=%s | takeover=%s",
            normalized_date,
            (lease or {}).get("lease_id") or "",
            normalized_owner_id,
            "yes" if previous_lease is not None else "no",
        )
        return lease

    def heartbeat_session_lease(
        self,
        session_date: str,
        lease_id: str,
        *,
        ttl_seconds: int = 180,
    ) -> bool:
        normalized_date = normalize_task_date(session_date, field_name="session_date")
        normalized_lease_id = _normalize_required_text(lease_id, "lease_id")
        normalized_ttl_seconds = max(
            30,
            _normalize_non_negative_int(ttl_seconds, field_name="ttl_seconds"),
        )
        now_dt = datetime.now()
        current_time = _format_timestamp(now_dt)
        expires_at = _format_timestamp(
            now_dt + timedelta(seconds=normalized_ttl_seconds)
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE autonomous_session_leases
                SET last_heartbeat_at = ?,
                    expires_at = ?,
                    updated_at = ?
                WHERE session_date = ?
                  AND lease_id = ?
                  AND COALESCE(released_at, '') = ''
                """,
                (
                    current_time,
                    expires_at,
                    current_time,
                    normalized_date,
                    normalized_lease_id,
                ),
            )
            connection.commit()
        return bool(cursor.rowcount)

    def release_session_lease(self, session_date: str, lease_id: str) -> bool:
        normalized_date = normalize_task_date(session_date, field_name="session_date")
        normalized_lease_id = _normalize_required_text(lease_id, "lease_id")
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE autonomous_session_leases
                SET released_at = ?,
                    expires_at = ?,
                    updated_at = ?
                WHERE session_date = ?
                  AND lease_id = ?
                  AND COALESCE(released_at, '') = ''
                """,
                (
                    current_time,
                    current_time,
                    current_time,
                    normalized_date,
                    normalized_lease_id,
                ),
            )
            connection.commit()
        if cursor.rowcount:
            logger.info(
                "Autonomous session lease released | session_date=%s | lease_id=%s",
                normalized_date,
                normalized_lease_id,
            )
        return bool(cursor.rowcount)

    def mark_plan_generated(self, session_date: str):
        normalized_date = normalize_task_date(session_date, field_name="session_date")
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            self._get_or_create_daily_session_locked(
                connection,
                normalized_date,
                current_time=current_time,
            )
            connection.execute(
                """
                UPDATE autonomous_daily_sessions
                SET plan_generated_at = COALESCE(plan_generated_at, ?),
                    updated_at = ?
                WHERE session_date = ?
                """,
                (
                    current_time,
                    current_time,
                    normalized_date,
                ),
            )
            connection.commit()
            session = self._get_session_locked(connection, normalized_date)
        return session

    def get_today_tasks(self, task_date: str) -> list[dict]:
        normalized_date = normalize_task_date(task_date)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM autonomous_tasks
                WHERE task_date = ?
                ORDER BY id ASC
                """,
                (normalized_date,),
            ).fetchall()
        return [self._serialize_task_row(row) for row in rows]

    def search_tasks(
        self,
        *,
        query: str = "",
        statuses=None,
        limit: int = 10,
        since_date=None,
        until_date=None,
        require_execution_log: bool = False,
    ) -> list[dict]:
        normalized_query = str(query or "").strip()
        normalized_limit = max(1, min(_normalize_non_negative_int(limit, field_name="limit") or 10, 50))
        normalized_statuses = []
        seen_statuses = set()
        for status in list(statuses or []):
            normalized_status = _normalize_status(
                status,
                valid_values=VALID_TASK_STATUSES,
                field_name="task status",
            )
            if normalized_status in seen_statuses:
                continue
            seen_statuses.add(normalized_status)
            normalized_statuses.append(normalized_status)

        clauses = []
        params = []
        if normalized_statuses:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(normalized_statuses)
        if since_date not in (None, ""):
            normalized_since_date = normalize_task_date(since_date, field_name="since_date")
            clauses.append("task_date >= ?")
            params.append(normalized_since_date)
        if until_date not in (None, ""):
            normalized_until_date = normalize_task_date(until_date, field_name="until_date")
            clauses.append("task_date <= ?")
            params.append(normalized_until_date)
        if require_execution_log:
            clauses.append("COALESCE(TRIM(execution_log), '') <> ''")
        if normalized_query:
            like_value = f"%{normalized_query}%"
            clauses.append(
                "("
                "task_content LIKE ? COLLATE NOCASE "
                "OR expected_goal LIKE ? COLLATE NOCASE "
                "OR execution_log LIKE ? COLLATE NOCASE"
                ")"
            )
            params.extend([like_value, like_value, like_value])

        sql = "SELECT * FROM autonomous_tasks"
        if clauses:
            sql = f"{sql} WHERE " + " AND ".join(clauses)
        sql = f"{sql} ORDER BY task_date DESC, id DESC LIMIT ?"
        params.append(normalized_limit)

        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self._serialize_task_row(row) for row in rows]

    def get_tasks_by_status(self, task_date: str, *, statuses) -> list[dict]:
        normalized_date = normalize_task_date(task_date)
        normalized_statuses = []
        seen_statuses = set()
        for status in list(statuses or []):
            normalized_status = _normalize_status(
                status,
                valid_values=VALID_TASK_STATUSES,
                field_name="task status",
            )
            if normalized_status in seen_statuses:
                continue
            normalized_statuses.append(normalized_status)
            seen_statuses.add(normalized_status)
        if not normalized_statuses:
            return []
        placeholders = ", ".join("?" for _ in normalized_statuses)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM autonomous_tasks
                WHERE task_date = ?
                  AND status IN ({placeholders})
                ORDER BY id ASC
                """,
                (normalized_date, *normalized_statuses),
            ).fetchall()
        return [self._serialize_task_row(row) for row in rows]

    def get_today_pending_tasks(self, task_date: str) -> list[dict]:
        return self.get_tasks_by_status(task_date, statuses=[TASK_STATUS_PENDING])

    def get_pending_carryover_tasks(self, from_date: str) -> list[dict]:
        normalized_date = normalize_task_date(from_date, field_name="from_date")
        carryover_statuses = [
            TASK_STATUS_PENDING,
            TASK_STATUS_PAUSED,
            TASK_STATUS_INTERRUPT_REQUESTED,
        ]
        placeholders = ", ".join("?" for _ in carryover_statuses)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT t.*
                FROM autonomous_tasks AS t
                LEFT JOIN autonomous_task_attempts AS a
                  ON a.attempt_id = t.current_attempt_id
                WHERE t.task_date = ?
                  AND (
                    t.status IN ({placeholders})
                    OR (t.status = ? AND COALESCE(a.status, '') = ?)
                  )
                ORDER BY t.id ASC
                """,
                (
                    normalized_date,
                    *carryover_statuses,
                    TASK_STATUS_RUNNING,
                    ATTEMPT_STATUS_STALE,
                ),
            ).fetchall()
        return [self._serialize_task_row(row) for row in rows]

    def create_task(
        self,
        *,
        task_date,
        task_content,
        expected_goal="",
        source=TASK_SOURCE_SELF_GENERATED,
        resume_snapshot="",
        carry_over_from_date=None,
        carry_over_from_id=None,
    ):
        normalized_task_date = normalize_task_date(task_date)
        normalized_task_content = _normalize_required_text(task_content, "task_content")
        normalized_expected_goal = str(_normalize_optional_input(expected_goal) or "")
        normalized_resume_snapshot = str(_normalize_optional_input(resume_snapshot) or "")
        normalized_source = _normalize_status(
            source,
            valid_values=VALID_TASK_SOURCES,
            field_name="task source",
        )
        normalized_carry_over_date = None
        normalized_carry_over_id = None
        if normalized_source == TASK_SOURCE_CARRIED_OVER:
            normalized_carry_over_date = normalize_task_date(
                carry_over_from_date,
                field_name="carry_over_from_date",
            )
            normalized_carry_over_id = _normalize_non_negative_int(
                carry_over_from_id,
                field_name="carry_over_from_id",
            )
            if normalized_carry_over_id <= 0:
                raise AutonomousTaskValidationError("carry_over_from_id must be > 0.")

        current_time = _now_text()
        with self._lock, self._connect() as connection:
            self._get_or_create_daily_session_locked(
                connection,
                normalized_task_date,
                current_time=current_time,
            )
            if normalized_source == TASK_SOURCE_CARRIED_OVER:
                existing = connection.execute(
                    """
                    SELECT * FROM autonomous_tasks
                    WHERE task_date = ?
                      AND source = ?
                      AND carry_over_from_date = ?
                      AND carry_over_from_id = ?
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (
                        normalized_task_date,
                        normalized_source,
                        normalized_carry_over_date,
                        normalized_carry_over_id,
                    ),
                ).fetchone()
                if existing is not None:
                    existing_task = self._serialize_task_row(existing)
                    logger.info(
                        "Reuse carried-over autonomous task | task_id=%s | task_date=%s | source=%s | task=%s",
                        existing_task.get("id"),
                        normalized_task_date,
                        normalized_source,
                        _preview_log_text(existing_task.get("task_content")),
                    )
                    return existing_task
            cursor = connection.execute(
                """
                INSERT INTO autonomous_tasks (
                    task_date,
                    task_content,
                    expected_goal,
                    status,
                    source,
                    resume_snapshot,
                    carry_over_from_date,
                    carry_over_from_id,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_task_date,
                    normalized_task_content,
                    normalized_expected_goal,
                    TASK_STATUS_PENDING,
                    normalized_source,
                    normalized_resume_snapshot,
                    normalized_carry_over_date,
                    normalized_carry_over_id,
                    current_time,
                    current_time,
                ),
            )
            connection.execute(
                """
                UPDATE autonomous_daily_sessions
                SET tasks_planned = tasks_planned + 1,
                    tasks_carried_over = tasks_carried_over + ?,
                    plan_generated_at = COALESCE(plan_generated_at, ?),
                    updated_at = ?
                WHERE session_date = ?
                """,
                (
                    1 if normalized_source == TASK_SOURCE_CARRIED_OVER else 0,
                    current_time,
                    current_time,
                    normalized_task_date,
                ),
            )
            connection.commit()
            created_id = cursor.lastrowid
            created_task = self._get_task_locked(connection, created_id)
        logger.info(
            "Created autonomous task | task_id=%s | task_date=%s | source=%s | task=%s | expected_goal=%s",
            (created_task or {}).get("id"),
            normalized_task_date,
            normalized_source,
            _preview_log_text(normalized_task_content),
            _preview_log_text(normalized_expected_goal),
        )
        return created_task

    def begin_task_attempt(self, task_id: int):
        current_time = _now_text()
        attempt_id = uuid.uuid4().hex
        lease_id = uuid.uuid4().hex
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            if task is None:
                raise AutonomousTaskValidationError(f"Unknown task_id: {task_id}")
            if task["status"] in TASK_TERMINAL_STATUSES:
                raise AutonomousTaskValidationError(
                    f"Task {task_id} is already terminal ({task['status']})."
                )
            if task["status"] not in TASK_RUNNABLE_STATUSES:
                raise AutonomousTaskValidationError(
                    f"Task {task_id} is not runnable from status '{task['status']}'."
                )
            current_attempt = self._get_attempt_locked(connection, task["current_attempt_id"])
            if current_attempt is not None and current_attempt["status"] in ATTEMPT_ACTIVE_STATUSES:
                raise AutonomousTaskValidationError(
                    f"Task {task_id} already has an active attempt."
                )

            connection.execute(
                """
                INSERT INTO autonomous_task_attempts (
                    attempt_id,
                    task_id,
                    task_date,
                    lease_id,
                    status,
                    started_at,
                    last_heartbeat_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    int(task_id),
                    task["task_date"],
                    lease_id,
                    ATTEMPT_STATUS_RUNNING,
                    current_time,
                    current_time,
                    current_time,
                ),
            )
            connection.execute(
                """
                UPDATE autonomous_tasks
                SET status = ?,
                    current_attempt_id = ?,
                    attempt_count = attempt_count + 1,
                    started_at = COALESCE(started_at, ?),
                    pause_reason = '',
                    pause_requested_at = NULL,
                    paused_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    TASK_STATUS_RUNNING,
                    attempt_id,
                    current_time,
                    current_time,
                    int(task_id),
                ),
            )
            connection.commit()
            created_attempt = self._get_attempt_locked(connection, attempt_id)
            next_attempt_count = int(task["attempt_count"] or 0) + 1
            task_date = str(task["task_date"] or "")
        logger.info(
            "Autonomous task attempt persisted | task_id=%s | attempt_id=%s | lease_id=%s | task_date=%s | attempt_count=%s",
            task_id,
            attempt_id,
            lease_id,
            task_date,
            next_attempt_count,
        )
        return created_attempt

    def heartbeat_attempt(self, task_id: int, attempt_id: str) -> None:
        normalized_attempt_id = _normalize_required_text(attempt_id, "attempt_id")
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            attempt = self._get_attempt_locked(connection, normalized_attempt_id)
            if task is None or attempt is None:
                return
            if task["current_attempt_id"] != normalized_attempt_id:
                return
            if attempt["status"] not in ATTEMPT_ACTIVE_STATUSES:
                return
            connection.execute(
                """
                UPDATE autonomous_task_attempts
                SET last_heartbeat_at = ?,
                    updated_at = ?
                WHERE attempt_id = ?
                """,
                (current_time, current_time, normalized_attempt_id),
            )
            connection.execute(
                """
                UPDATE autonomous_tasks
                SET updated_at = ?
                WHERE id = ?
                """,
                (current_time, int(task_id)),
            )
            connection.commit()

    def persist_attempt_runtime_task_id(
        self,
        task_id: int,
        attempt_id: str,
        lease_id: str,
        subagent_task_id: str,
    ):
        normalized_attempt_id = _normalize_required_text(attempt_id, "attempt_id")
        normalized_lease_id = _normalize_required_text(lease_id, "lease_id")
        normalized_subagent_task_id = _normalize_required_text(
            subagent_task_id,
            "subagent_task_id",
        )
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            attempt = self._get_attempt_locked(connection, normalized_attempt_id)
            if task is None or attempt is None:
                return attempt
            if task["current_attempt_id"] != normalized_attempt_id:
                return attempt
            if attempt["lease_id"] != normalized_lease_id:
                return attempt
            connection.execute(
                """
                UPDATE autonomous_task_attempts
                SET subagent_task_id = ?,
                    updated_at = ?
                WHERE attempt_id = ?
                """,
                (
                    normalized_subagent_task_id,
                    current_time,
                    normalized_attempt_id,
                ),
            )
            connection.commit()
            attempt = self._get_attempt_locked(connection, normalized_attempt_id)
        logger.info(
            "Autonomous attempt runtime id persisted | task_id=%s | attempt_id=%s | runtime_task_id=%s",
            task_id,
            normalized_attempt_id,
            normalized_subagent_task_id,
        )
        return attempt

    def _mark_attempt_stale_locked(self, connection, attempt_id: str, *, current_time: str):
        connection.execute(
            """
            UPDATE autonomous_task_attempts
            SET status = ?,
                finished_at = COALESCE(finished_at, ?),
                updated_at = ?
            WHERE attempt_id = ?
            """,
            (
                ATTEMPT_STATUS_STALE,
                current_time,
                current_time,
                attempt_id,
            ),
        )

    def _transition_task_to_paused_locked(
        self,
        connection,
        task_id: int,
        *,
        reason: str,
        resume_snapshot: str = "",
        current_time: str,
    ):
        task = self._get_task_locked(connection, task_id)
        if task is None or task["status"] in TASK_TERMINAL_STATUSES:
            return task
        next_resume_snapshot = str(resume_snapshot or task["resume_snapshot"] or "")
        connection.execute(
            """
            UPDATE autonomous_tasks
            SET status = ?,
                pause_reason = ?,
                resume_snapshot = ?,
                pause_requested_at = COALESCE(pause_requested_at, ?),
                paused_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                TASK_STATUS_PAUSED,
                str(reason or ""),
                next_resume_snapshot,
                current_time,
                current_time,
                current_time,
                int(task_id),
            ),
        )
        return self._get_task_locked(connection, task_id)

    def request_task_interrupt(self, task_id: int, attempt_id: str, *, reason: str):
        normalized_attempt_id = _normalize_required_text(attempt_id, "attempt_id")
        normalized_reason = str(_normalize_optional_input(reason) or "")
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            attempt = self._get_attempt_locked(connection, normalized_attempt_id)
            if task is None or attempt is None:
                return task
            if task["current_attempt_id"] != normalized_attempt_id:
                return task
            if task["status"] in TASK_TERMINAL_STATUSES:
                return task
            if attempt["status"] in ATTEMPT_ACTIVE_STATUSES:
                connection.execute(
                    """
                    UPDATE autonomous_task_attempts
                    SET status = ?,
                        last_heartbeat_at = ?,
                        updated_at = ?
                    WHERE attempt_id = ?
                    """,
                    (
                        ATTEMPT_STATUS_INTERRUPT_REQUESTED,
                        current_time,
                        current_time,
                        normalized_attempt_id,
                    ),
                )
            connection.execute(
                """
                UPDATE autonomous_tasks
                SET status = ?,
                    pause_reason = ?,
                    pause_requested_at = COALESCE(pause_requested_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    TASK_STATUS_INTERRUPT_REQUESTED,
                    normalized_reason,
                    current_time,
                    current_time,
                    int(task_id),
                ),
            )
            connection.commit()
            updated_task = self._get_task_locked(connection, task_id)
        logger.info(
            "Autonomous task interrupt requested | task_id=%s | attempt_id=%s | reason=%s | status=%s",
            task_id,
            normalized_attempt_id,
            normalized_reason,
            (updated_task or {}).get("status") or "",
        )
        return updated_task

    def mark_task_paused(
        self,
        task_id: int,
        attempt_id: str,
        *,
        reason: str,
        resume_snapshot: str = "",
    ):
        normalized_attempt_id = _normalize_required_text(attempt_id, "attempt_id")
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            attempt = self._get_attempt_locked(connection, normalized_attempt_id)
            if task is None or attempt is None:
                return task
            if task["current_attempt_id"] != normalized_attempt_id:
                return task
            if attempt["status"] not in ATTEMPT_TERMINAL_STATUSES:
                connection.execute(
                    """
                    UPDATE autonomous_task_attempts
                    SET status = ?,
                        error_message = CASE
                            WHEN COALESCE(error_message, '') = '' THEN ?
                            ELSE error_message
                        END,
                        finished_at = COALESCE(finished_at, ?),
                        last_heartbeat_at = ?,
                        updated_at = ?
                    WHERE attempt_id = ?
                    """,
                    (
                        ATTEMPT_STATUS_CANCELLED,
                        str(reason or ""),
                        current_time,
                        current_time,
                        current_time,
                        normalized_attempt_id,
                    ),
                )
            self._transition_task_to_paused_locked(
                connection,
                task_id,
                reason=str(reason or ""),
                resume_snapshot=str(resume_snapshot or task["execution_log"] or ""),
                current_time=current_time,
            )
            connection.commit()
            paused_task = self._get_task_locked(connection, task_id)
        logger.info(
            "Autonomous task paused | task_id=%s | attempt_id=%s | reason=%s | status=%s",
            task_id,
            normalized_attempt_id,
            str(reason or ""),
            (paused_task or {}).get("status") or "",
        )
        return paused_task

    def mark_task_paused_pending_attempt_exit(
        self,
        task_id: int,
        attempt_id: str,
        *,
        reason: str,
        resume_snapshot: str = "",
    ):
        """Pause the task immediately but keep the current attempt active until runtime confirms exit."""
        normalized_attempt_id = _normalize_required_text(attempt_id, "attempt_id")
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            attempt = self._get_attempt_locked(connection, normalized_attempt_id)
            if task is None or attempt is None:
                return task
            if task["current_attempt_id"] != normalized_attempt_id:
                return task
            self._transition_task_to_paused_locked(
                connection,
                task_id,
                reason=str(reason or ""),
                resume_snapshot=str(
                    resume_snapshot
                    or task["execution_log"]
                    or task["resume_snapshot"]
                    or ""
                ),
                current_time=current_time,
            )
            connection.commit()
            paused_task = self._get_task_locked(connection, task_id)
        logger.warning(
            "Autonomous task paused pending runtime exit | task_id=%s | attempt_id=%s | reason=%s | status=%s",
            task_id,
            normalized_attempt_id,
            str(reason or ""),
            (paused_task or {}).get("status") or "",
        )
        return paused_task

    def _complete_task_locked(self, connection, task_id: int, *, execution_log: str, current_time: str):
        task = self._get_task_locked(connection, task_id)
        if task is None:
            return None
        previous_status = task["status"]
        connection.execute(
            """
            UPDATE autonomous_tasks
            SET status = ?,
                execution_log = ?,
                pause_reason = '',
                completed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                TASK_STATUS_COMPLETED,
                str(execution_log or ""),
                current_time,
                current_time,
                int(task_id),
            ),
        )
        current_attempt_id = str(task["current_attempt_id"] or "")
        if current_attempt_id:
            connection.execute(
                """
                UPDATE autonomous_task_attempts
                SET status = CASE
                        WHEN status IN (?, ?, ?, ?, ?)
                        THEN status
                        ELSE ?
                    END,
                    result_summary = CASE
                        WHEN COALESCE(result_summary, '') = '' THEN ?
                        ELSE result_summary
                    END,
                    finished_at = COALESCE(finished_at, ?),
                    updated_at = ?
                WHERE attempt_id = ?
                """,
                (
                    ATTEMPT_STATUS_COMPLETED,
                    ATTEMPT_STATUS_FAILED,
                    ATTEMPT_STATUS_CANCELLED,
                    ATTEMPT_STATUS_TIMED_OUT,
                    ATTEMPT_STATUS_STALE,
                    ATTEMPT_STATUS_COMPLETED,
                    str(execution_log or ""),
                    current_time,
                    current_time,
                    current_attempt_id,
                ),
            )
        if previous_status != TASK_STATUS_COMPLETED:
            self._get_or_create_daily_session_locked(
                connection,
                task["task_date"],
                current_time=current_time,
            )
            connection.execute(
                """
                UPDATE autonomous_daily_sessions
                SET tasks_completed = tasks_completed + 1,
                    updated_at = ?
                WHERE session_date = ?
                """,
                (current_time, task["task_date"]),
            )
        return self._get_task_locked(connection, task_id)

    def complete_task(self, task_id: int, *, execution_log: str):
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._complete_task_locked(
                connection,
                task_id,
                execution_log=execution_log,
                current_time=current_time,
            )
            connection.commit()
        logger.info(
            "Autonomous task completed | task_id=%s | status=%s | execution_log=%s",
            task_id,
            (task or {}).get("status") or "",
            _preview_log_text(execution_log),
        )
        return task

    def _fail_task_locked(self, connection, task_id: int, *, error: str, current_time: str):
        task = self._get_task_locked(connection, task_id)
        if task is None:
            return None
        failure_text = str(error or "")
        next_execution_log = str(task["execution_log"] or "")
        if not next_execution_log and failure_text:
            next_execution_log = failure_text
        connection.execute(
            """
            UPDATE autonomous_tasks
            SET status = ?,
                execution_log = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                TASK_STATUS_FAILED,
                next_execution_log,
                current_time,
                int(task_id),
            ),
        )
        current_attempt_id = str(task["current_attempt_id"] or "")
        if current_attempt_id:
            connection.execute(
                """
                UPDATE autonomous_task_attempts
                SET status = CASE
                        WHEN status IN (?, ?, ?, ?, ?)
                        THEN status
                        ELSE ?
                    END,
                    error_message = CASE
                        WHEN COALESCE(error_message, '') = '' THEN ?
                        ELSE error_message
                    END,
                    finished_at = COALESCE(finished_at, ?),
                    updated_at = ?
                WHERE attempt_id = ?
                """,
                (
                    ATTEMPT_STATUS_COMPLETED,
                    ATTEMPT_STATUS_FAILED,
                    ATTEMPT_STATUS_CANCELLED,
                    ATTEMPT_STATUS_TIMED_OUT,
                    ATTEMPT_STATUS_STALE,
                    ATTEMPT_STATUS_FAILED,
                    failure_text,
                    current_time,
                    current_time,
                    current_attempt_id,
                ),
            )
        return self._get_task_locked(connection, task_id)

    def fail_task(self, task_id: int, *, error: str):
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._fail_task_locked(
                connection,
                task_id,
                error=error,
                current_time=current_time,
            )
            connection.commit()
        logger.warning(
            "Autonomous task failed | task_id=%s | status=%s | error=%s",
            task_id,
            (task or {}).get("status") or "",
            _preview_log_text(error),
        )
        return task

    def mark_carried_over(self, task_id: int):
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            if task is None:
                return None
            if task["status"] == TASK_STATUS_CARRIED_OVER:
                return task
            connection.execute(
                """
                UPDATE autonomous_tasks
                SET status = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    TASK_STATUS_CARRIED_OVER,
                    current_time,
                    int(task_id),
                ),
            )
            connection.commit()
        return self.get_task(task_id)

    def update_task_resume_snapshot(self, task_id: int, resume_snapshot: str):
        normalized_resume_snapshot = str(_normalize_optional_input(resume_snapshot) or "")
        if not normalized_resume_snapshot:
            return self.get_task(task_id)
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            if task is None:
                return None
            connection.execute(
                """
                UPDATE autonomous_tasks
                SET resume_snapshot = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized_resume_snapshot,
                    current_time,
                    int(task_id),
                ),
            )
            connection.commit()
        logger.info(
            "Autonomous task resume snapshot updated | task_id=%s | chars=%s",
            task_id,
            len(normalized_resume_snapshot),
        )
        return self.get_task(task_id)

    def _is_attempt_stale(self, attempt: dict | None, stale_timeout_seconds: int) -> bool:
        if not attempt:
            return True
        if attempt["status"] not in ATTEMPT_ACTIVE_STATUSES:
            return True
        timeout_seconds = max(0, int(stale_timeout_seconds or 0))
        last_activity = (
            _parse_timestamp(attempt.get("last_heartbeat_at"))
            or _parse_timestamp(attempt.get("updated_at"))
            or _parse_timestamp(attempt.get("started_at"))
        )
        if last_activity is None:
            return True
        return (datetime.now() - last_activity).total_seconds() >= timeout_seconds

    def reconcile_incomplete_tasks(self, task_date: str, *, stale_timeout_seconds: int = 600) -> int:
        normalized_date = normalize_task_date(task_date)
        current_time = _now_text()
        paused_count = 0
        with self._lock, self._connect() as connection:
            active_tasks = self.get_tasks_by_status(
                normalized_date,
                statuses=[TASK_STATUS_RUNNING, TASK_STATUS_INTERRUPT_REQUESTED],
            )
            for task in active_tasks:
                current_attempt = self._get_attempt_locked(
                    connection,
                    task["current_attempt_id"],
                )
                if not self._is_attempt_stale(current_attempt, stale_timeout_seconds):
                    continue
                if current_attempt is not None and current_attempt["status"] in ATTEMPT_ACTIVE_STATUSES:
                    self._mark_attempt_stale_locked(
                        connection,
                        current_attempt["attempt_id"],
                        current_time=current_time,
                    )
                self._transition_task_to_paused_locked(
                    connection,
                    task["id"],
                    reason="stale_attempt_reconciled",
                    resume_snapshot=str(task.get("execution_log") or ""),
                    current_time=current_time,
                )
                paused_count += 1
            connection.commit()
        if paused_count > 0:
            logger.warning(
                "Reconciled incomplete autonomous tasks | session_date=%s | paused_task_count=%s | stale_timeout_seconds=%s",
                normalized_date,
                paused_count,
                stale_timeout_seconds,
            )
        return paused_count

    def get_today_runnable_tasks(self, task_date: str) -> list[dict]:
        normalized_date = normalize_task_date(task_date)
        runnable_tasks = self.get_tasks_by_status(
            normalized_date,
            statuses=sorted(TASK_RUNNABLE_STATUSES),
        )
        filtered_tasks = []
        with self._lock, self._connect() as connection:
            for task in runnable_tasks:
                current_attempt = self._get_attempt_locked(
                    connection,
                    task["current_attempt_id"],
                )
                if current_attempt is not None and current_attempt["status"] in ATTEMPT_ACTIVE_STATUSES:
                    continue
                filtered_tasks.append(task)
        return filtered_tasks

    def record_token_usage(self, task_id: int, *, input_tokens: int, output_tokens: int):
        normalized_input_tokens = _normalize_non_negative_int(
            input_tokens,
            field_name="input_tokens",
        )
        normalized_output_tokens = _normalize_non_negative_int(
            output_tokens,
            field_name="output_tokens",
        )
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            if task is None:
                return None
            connection.execute(
                """
                UPDATE autonomous_tasks
                SET token_usage_input = token_usage_input + ?,
                    token_usage_output = token_usage_output + ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    normalized_input_tokens,
                    normalized_output_tokens,
                    current_time,
                    int(task_id),
                ),
            )
            current_attempt_id = str(task["current_attempt_id"] or "")
            if current_attempt_id:
                connection.execute(
                    """
                    UPDATE autonomous_task_attempts
                    SET input_tokens = input_tokens + ?,
                        output_tokens = output_tokens + ?,
                        updated_at = ?
                    WHERE attempt_id = ?
                    """,
                    (
                        normalized_input_tokens,
                        normalized_output_tokens,
                        current_time,
                        current_attempt_id,
                    ),
                )
            connection.commit()
        return self.get_task(task_id)

    def add_session_tokens(self, session_date: str, *, input_tokens: int, output_tokens: int) -> None:
        normalized_date = normalize_task_date(session_date, field_name="session_date")
        normalized_input_tokens = _normalize_non_negative_int(
            input_tokens,
            field_name="input_tokens",
        )
        normalized_output_tokens = _normalize_non_negative_int(
            output_tokens,
            field_name="output_tokens",
        )
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            self._get_or_create_daily_session_locked(
                connection,
                normalized_date,
                current_time=current_time,
            )
            connection.execute(
                """
                UPDATE autonomous_daily_sessions
                SET total_input_tokens = total_input_tokens + ?,
                    total_output_tokens = total_output_tokens + ?,
                    updated_at = ?
                WHERE session_date = ?
                """,
                (
                    normalized_input_tokens,
                    normalized_output_tokens,
                    current_time,
                    normalized_date,
                ),
            )
            connection.commit()

    def increment_interrupt_count(self, session_date: str) -> int:
        normalized_date = normalize_task_date(session_date, field_name="session_date")
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            self._get_or_create_daily_session_locked(
                connection,
                normalized_date,
                current_time=current_time,
            )
            connection.execute(
                """
                UPDATE autonomous_daily_sessions
                SET interrupt_count = interrupt_count + 1,
                    updated_at = ?
                WHERE session_date = ?
                """,
                (current_time, normalized_date),
            )
            connection.commit()
            session = self._get_session_locked(connection, normalized_date)
        return int((session or {}).get("interrupt_count", 0) or 0)

    def get_daily_interrupt_count(self, session_date: str) -> int:
        normalized_date = normalize_task_date(session_date, field_name="session_date")
        with self._lock, self._connect() as connection:
            session = self._get_session_locked(connection, normalized_date)
        return int((session or {}).get("interrupt_count", 0) or 0)

    def finish_daily_session(self, session_date: str, *, finish_reason: str):
        normalized_date = normalize_task_date(session_date, field_name="session_date")
        normalized_reason = str(_normalize_optional_input(finish_reason) or "")
        if normalized_reason not in SESSION_FINISH_REASONS:
            raise AutonomousTaskValidationError(
                f"Invalid finish_reason '{finish_reason}'."
            )
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            self._get_or_create_daily_session_locked(
                connection,
                normalized_date,
                current_time=current_time,
            )
            connection.execute(
                """
                UPDATE autonomous_daily_sessions
                SET session_finished_at = COALESCE(session_finished_at, ?),
                    finish_reason = CASE
                        WHEN COALESCE(finish_reason, '') = '' THEN ?
                        ELSE finish_reason
                    END,
                    updated_at = ?
                WHERE session_date = ?
                """,
                (
                    current_time,
                    normalized_reason,
                    current_time,
                    normalized_date,
                ),
            )
            connection.commit()
            session = self._get_session_locked(connection, normalized_date)
        logger.info(
            "Autonomous daily session persisted | session_date=%s | finish_reason=%s | tasks_planned=%s | tasks_completed=%s | tasks_carried_over=%s | interrupt_count=%s | input_tokens=%s | output_tokens=%s",
            normalized_date,
            normalized_reason,
            (session or {}).get("tasks_planned", 0),
            (session or {}).get("tasks_completed", 0),
            (session or {}).get("tasks_carried_over", 0),
            (session or {}).get("interrupt_count", 0),
            (session or {}).get("total_input_tokens", 0),
            (session or {}).get("total_output_tokens", 0),
        )
        return session

    def finalize_attempt_from_runtime(
        self,
        task_id: int,
        attempt_id: str,
        lease_id: str,
        task_result: dict,
    ):
        normalized_attempt_id = _normalize_required_text(attempt_id, "attempt_id")
        normalized_lease_id = _normalize_required_text(lease_id, "lease_id")
        normalized_result = dict(task_result or {})
        incoming_status = str(
            _normalize_optional_input(normalized_result.get("status")) or ""
        ).lower()
        if incoming_status in VALID_ATTEMPT_STATUSES:
            resolved_attempt_status = incoming_status
        elif normalized_result.get("error"):
            resolved_attempt_status = ATTEMPT_STATUS_FAILED
        else:
            resolved_attempt_status = ATTEMPT_STATUS_COMPLETED
        current_time = _now_text()
        with self._lock, self._connect() as connection:
            task = self._get_task_locked(connection, task_id)
            attempt = self._get_attempt_locked(connection, normalized_attempt_id)
            if task is None or attempt is None:
                return task
            if attempt["lease_id"] != normalized_lease_id:
                return task
            if task["current_attempt_id"] != normalized_attempt_id:
                return task

            result_summary = str(
                normalized_result.get("result_summary")
                or normalized_result.get("result")
                or normalized_result.get("status_message")
                or ""
            )
            error_message = str(normalized_result.get("error") or "")
            execution_log = str(
                normalized_result.get("execution_log")
                or result_summary
                or task["execution_log"]
                or ""
            )
            resume_snapshot = str(
                normalized_result.get("resume_snapshot")
                or execution_log
                or task["resume_snapshot"]
                or ""
            )
            subagent_task_id = str(normalized_result.get("task_id") or "")
            connection.execute(
                """
                UPDATE autonomous_task_attempts
                SET subagent_task_id = CASE
                        WHEN COALESCE(subagent_task_id, '') = '' THEN ?
                        ELSE subagent_task_id
                    END,
                    status = ?,
                    result_summary = ?,
                    error_message = ?,
                    last_heartbeat_at = ?,
                    finished_at = COALESCE(finished_at, ?),
                    updated_at = ?
                WHERE attempt_id = ?
                """,
                (
                    subagent_task_id,
                    resolved_attempt_status,
                    result_summary,
                    error_message,
                    current_time,
                    current_time,
                    current_time,
                    normalized_attempt_id,
                ),
            )

            if resolved_attempt_status == ATTEMPT_STATUS_COMPLETED:
                task = self._complete_task_locked(
                    connection,
                    task_id,
                    execution_log=execution_log,
                    current_time=current_time,
                )
            elif resolved_attempt_status == ATTEMPT_STATUS_FAILED:
                task = self._fail_task_locked(
                    connection,
                    task_id,
                    error=error_message or "Autonomous task failed.",
                    current_time=current_time,
                )
            elif resolved_attempt_status in {
                ATTEMPT_STATUS_CANCELLED,
                ATTEMPT_STATUS_TIMED_OUT,
                ATTEMPT_STATUS_STALE,
                ATTEMPT_STATUS_INTERRUPT_REQUESTED,
            }:
                task = self._transition_task_to_paused_locked(
                    connection,
                    task_id,
                    reason=error_message or incoming_status or "attempt_interrupted",
                    resume_snapshot=resume_snapshot,
                    current_time=current_time,
                )
            else:
                connection.execute(
                    """
                    UPDATE autonomous_tasks
                    SET updated_at = ?
                    WHERE id = ?
                    """,
                    (current_time, int(task_id)),
                )
                task = self._get_task_locked(connection, task_id)
            connection.commit()
        logger.info(
            "Autonomous attempt finalized from runtime | task_id=%s | attempt_id=%s | runtime_task_id=%s | attempt_status=%s | task_status=%s | result=%s | error=%s",
            task_id,
            normalized_attempt_id,
            subagent_task_id,
            resolved_attempt_status,
            (task or {}).get("status") or "",
            _preview_log_text(result_summary),
            _preview_log_text(error_message),
        )
        return task
