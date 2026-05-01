"""SQLite-backed schedule storage and reminder helpers for DialogueSystem."""

import os
import sqlite3
import threading
from datetime import date, datetime, time as dt_time


REMINDER_STATUS_UNREMIND = "未提醒"
REMINDER_STATUS_REMINDED = "已提醒"
VALID_REMINDER_STATUSES = {
    REMINDER_STATUS_UNREMIND,
    REMINDER_STATUS_REMINDED,
}

TASK_STATUS_PENDING = "未完成"
TASK_STATUS_COMPLETED = "已完成"
TASK_STATUS_DELAYED = "延迟"
TASK_STATUS_CANCELLED = "取消"
VALID_TASK_STATUSES = {
    TASK_STATUS_PENDING,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DELAYED,
    TASK_STATUS_CANCELLED,
}
ACTIVE_TASK_STATUSES = {
    TASK_STATUS_PENDING,
    TASK_STATUS_DELAYED,
}

DATETIME_OUTPUT_FORMAT = "%Y-%m-%d %H:%M:%S"
DATE_OUTPUT_FORMAT = "%Y-%m-%d"
DATE_INPUT_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
)
DATETIME_INPUT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
)
TIME_INPUT_FORMATS = (
    "%H:%M:%S",
    "%H:%M",
)


class ScheduleValidationError(ValueError):
    """Raised when a schedule payload is malformed."""


def _normalize_optional_input(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def normalize_task_date(value) -> str:
    normalized = _normalize_optional_input(value)
    if normalized is None:
        raise ScheduleValidationError("TaskDate is required.")

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
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime(DATE_OUTPUT_FORMAT)
    except ValueError as exc:
        raise ScheduleValidationError(
            f"Invalid TaskDate '{text}'. Use YYYY-MM-DD or YYYY/MM/DD."
        ) from exc


def _parse_time_only(value: str):
    for fmt in TIME_INPUT_FORMATS:
        try:
            return datetime.strptime(value, fmt).time()
        except ValueError:
            continue
    return None


def normalize_reminder_time(value, *, task_date: str = None) -> str:
    normalized = _normalize_optional_input(value)
    if normalized is None:
        raise ScheduleValidationError("ReminderTime is required.")

    if isinstance(normalized, datetime):
        return normalized.strftime(DATETIME_OUTPUT_FORMAT)

    if isinstance(normalized, dt_time):
        if not task_date:
            raise ScheduleValidationError("TaskDate is required when ReminderTime only contains time.")
        return datetime.combine(
            datetime.strptime(task_date, DATE_OUTPUT_FORMAT).date(),
            normalized
        ).strftime(DATETIME_OUTPUT_FORMAT)

    text = str(normalized)
    parsed_time = _parse_time_only(text)
    if parsed_time is not None:
        if not task_date:
            raise ScheduleValidationError("TaskDate is required when ReminderTime only contains time.")
        return datetime.combine(
            datetime.strptime(task_date, DATE_OUTPUT_FORMAT).date(),
            parsed_time
        ).strftime(DATETIME_OUTPUT_FORMAT)

    for fmt in DATETIME_INPUT_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime(DATETIME_OUTPUT_FORMAT)
        except ValueError:
            continue

    try:
        parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScheduleValidationError(
            f"Invalid ReminderTime '{text}'. Use YYYY-MM-DD HH:MM[:SS] or HH:MM[:SS]."
        ) from exc

    if parsed_datetime.tzinfo is not None:
        parsed_datetime = parsed_datetime.astimezone().replace(tzinfo=None)
    return parsed_datetime.strftime(DATETIME_OUTPUT_FORMAT)


def normalize_reminder_status(value, *, allow_none: bool = False):
    normalized = _normalize_optional_input(value)
    if normalized is None and allow_none:
        return None
    if normalized not in VALID_REMINDER_STATUSES:
        raise ScheduleValidationError(
            f"Invalid ReminderStatus '{value}'. Valid values: {sorted(VALID_REMINDER_STATUSES)}."
        )
    return normalized


def normalize_task_status(value, *, allow_none: bool = False):
    normalized = _normalize_optional_input(value)
    if normalized is None and allow_none:
        return None
    if normalized not in VALID_TASK_STATUSES:
        raise ScheduleValidationError(
            f"Invalid TaskStatus '{value}'. Valid values: {sorted(VALID_TASK_STATUSES)}."
        )
    return normalized


def align_task_date_and_reminder_time(task_date_value, reminder_time_value):
    normalized_task_date = normalize_task_date(task_date_value)
    normalized_reminder_time = normalize_reminder_time(reminder_time_value, task_date=normalized_task_date)
    reminder_date = datetime.strptime(normalized_reminder_time, DATETIME_OUTPUT_FORMAT).strftime(DATE_OUTPUT_FORMAT)
    if reminder_date != normalized_task_date:
        raise ScheduleValidationError(
            "TaskDate must match the date part of ReminderTime."
        )
    return normalized_task_date, normalized_reminder_time


class ScheduleRepository:
    """Handles schedule CRUD and reminder queries backed by SQLite."""

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
                CREATE TABLE IF NOT EXISTS schedule_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_date TEXT NOT NULL,
                    reminder_time TEXT NOT NULL,
                    task_content TEXT NOT NULL,
                    reminder_status TEXT NOT NULL DEFAULT '未提醒',
                    task_status TEXT NOT NULL DEFAULT '未完成',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reminded_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_schedule_tasks_due
                ON schedule_tasks (reminder_status, reminder_time, task_status)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_schedule_tasks_date
                ON schedule_tasks (task_date, reminder_time)
                """
            )
            connection.commit()

    @staticmethod
    def _serialize_row(row):
        if row is None:
            return None
        return {
            "task_id": row["id"],
            "task_date": row["task_date"],
            "reminder_time": row["reminder_time"],
            "task_content": row["task_content"],
            "reminder_status": row["reminder_status"],
            "task_status": row["task_status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "reminded_at": row["reminded_at"],
        }

    def get_task(self, task_id: int):
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM schedule_tasks WHERE id = ?",
                (int(task_id),)
            ).fetchone()
        return self._serialize_row(row)

    def list_tasks(
        self,
        *,
        task_id=None,
        task_date=None,
        reminder_status=None,
        task_status=None,
        limit: int = 50
    ):
        normalized_limit = max(1, min(int(limit or 50), 200))
        sql = ["SELECT * FROM schedule_tasks WHERE 1=1"]
        params = []

        normalized_task_id = _normalize_optional_input(task_id)
        if normalized_task_id is not None:
            sql.append("AND id = ?")
            params.append(int(normalized_task_id))

        normalized_task_date = _normalize_optional_input(task_date)
        if normalized_task_date is not None:
            sql.append("AND task_date = ?")
            params.append(normalize_task_date(normalized_task_date))

        normalized_reminder_status = normalize_reminder_status(reminder_status, allow_none=True)
        if normalized_reminder_status is not None:
            sql.append("AND reminder_status = ?")
            params.append(normalized_reminder_status)

        normalized_task_status = normalize_task_status(task_status, allow_none=True)
        if normalized_task_status is not None:
            sql.append("AND task_status = ?")
            params.append(normalized_task_status)

        sql.append("ORDER BY reminder_time ASC, id ASC LIMIT ?")
        params.append(normalized_limit)

        with self._lock, self._connect() as connection:
            rows = connection.execute(" ".join(sql), tuple(params)).fetchall()
        return [self._serialize_row(row) for row in rows]

    def create_task(
        self,
        *,
        task_date,
        reminder_time,
        task_content,
        reminder_status=REMINDER_STATUS_UNREMIND,
        task_status=TASK_STATUS_PENDING
    ):
        normalized_task_date, normalized_reminder_time = align_task_date_and_reminder_time(
            task_date,
            reminder_time
        )
        normalized_task_content = str(_normalize_optional_input(task_content) or "")
        if not normalized_task_content:
            raise ScheduleValidationError("TaskContent is required.")

        normalized_reminder_status = normalize_reminder_status(reminder_status)
        normalized_task_status = normalize_task_status(task_status)
        current_time = datetime.now().strftime(DATETIME_OUTPUT_FORMAT)

        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO schedule_tasks (
                    task_date,
                    reminder_time,
                    task_content,
                    reminder_status,
                    task_status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_task_date,
                    normalized_reminder_time,
                    normalized_task_content,
                    normalized_reminder_status,
                    normalized_task_status,
                    current_time,
                    current_time,
                )
            )
            connection.commit()
            created_id = cursor.lastrowid
        return self.get_task(created_id)

    def update_task(
        self,
        task_id: int,
        *,
        task_date=None,
        reminder_time=None,
        task_content=None,
        reminder_status=None,
        task_status=None
    ):
        existing_task = self.get_task(task_id)
        if existing_task is None:
            return None

        normalized_task_content = existing_task["task_content"]
        if _normalize_optional_input(task_content) is not None:
            normalized_task_content = str(_normalize_optional_input(task_content))

        normalized_task_status = normalize_task_status(
            task_status,
            allow_none=True
        ) or existing_task["task_status"]

        task_date_provided = _normalize_optional_input(task_date) is not None
        reminder_time_provided = _normalize_optional_input(reminder_time) is not None

        if reminder_time_provided:
            base_task_date = task_date if task_date_provided else existing_task["task_date"]
            normalized_task_date, normalized_reminder_time = align_task_date_and_reminder_time(
                base_task_date,
                reminder_time
            )
        elif task_date_provided:
            normalized_task_date = normalize_task_date(task_date)
            existing_reminder_dt = datetime.strptime(
                existing_task["reminder_time"],
                DATETIME_OUTPUT_FORMAT
            )
            normalized_reminder_time = datetime.combine(
                datetime.strptime(normalized_task_date, DATE_OUTPUT_FORMAT).date(),
                existing_reminder_dt.time()
            ).strftime(DATETIME_OUTPUT_FORMAT)
        else:
            normalized_task_date = existing_task["task_date"]
            normalized_reminder_time = existing_task["reminder_time"]

        normalized_reminder_status = normalize_reminder_status(
            reminder_status,
            allow_none=True
        )
        if normalized_reminder_status is None:
            if task_date_provided or reminder_time_provided:
                if normalized_task_status in ACTIVE_TASK_STATUSES:
                    normalized_reminder_status = REMINDER_STATUS_UNREMIND
                else:
                    normalized_reminder_status = existing_task["reminder_status"]
            else:
                normalized_reminder_status = existing_task["reminder_status"]

        reminded_at = existing_task["reminded_at"]
        if normalized_reminder_status == REMINDER_STATUS_UNREMIND:
            reminded_at = None
        elif existing_task["reminder_status"] != REMINDER_STATUS_REMINDED:
            reminded_at = datetime.now().strftime(DATETIME_OUTPUT_FORMAT)

        updated_at = datetime.now().strftime(DATETIME_OUTPUT_FORMAT)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE schedule_tasks
                SET task_date = ?,
                    reminder_time = ?,
                    task_content = ?,
                    reminder_status = ?,
                    task_status = ?,
                    updated_at = ?,
                    reminded_at = ?
                WHERE id = ?
                """,
                (
                    normalized_task_date,
                    normalized_reminder_time,
                    normalized_task_content,
                    normalized_reminder_status,
                    normalized_task_status,
                    updated_at,
                    reminded_at,
                    int(task_id),
                )
            )
            connection.commit()
        return self.get_task(task_id)

    def delete_task(self, task_id: int) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM schedule_tasks WHERE id = ?",
                (int(task_id),)
            )
            connection.commit()
        return cursor.rowcount > 0

    def get_due_unreminded_tasks(self, *, now=None, limit: int = 20):
        normalized_limit = max(1, min(int(limit or 20), 200))
        now_text = normalize_reminder_time(now or datetime.now(), task_date=datetime.now().strftime(DATE_OUTPUT_FORMAT))
        placeholders = ", ".join("?" for _ in ACTIVE_TASK_STATUSES)
        sql = f"""
            SELECT * FROM schedule_tasks
            WHERE reminder_status = ?
              AND task_status IN ({placeholders})
              AND reminder_time <= ?
            ORDER BY reminder_time ASC, id ASC
            LIMIT ?
        """
        params = [
            REMINDER_STATUS_UNREMIND,
            *sorted(ACTIVE_TASK_STATUSES),
            now_text,
            normalized_limit,
        ]
        with self._lock, self._connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self._serialize_row(row) for row in rows]

    def mark_tasks_reminded(self, task_ids, *, reminded_at=None):
        normalized_ids = sorted({int(task_id) for task_id in task_ids or []})
        if not normalized_ids:
            return 0
        placeholders = ", ".join("?" for _ in normalized_ids)
        reminded_time = normalize_reminder_time(
            reminded_at or datetime.now(),
            task_date=datetime.now().strftime(DATE_OUTPUT_FORMAT)
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE schedule_tasks
                SET reminder_status = ?,
                    reminded_at = ?,
                    updated_at = ?
                WHERE id IN ({placeholders})
                  AND reminder_status = ?
                """,
                (
                    REMINDER_STATUS_REMINDED,
                    reminded_time,
                    reminded_time,
                    *normalized_ids,
                    REMINDER_STATUS_UNREMIND,
                )
            )
            connection.commit()
        return cursor.rowcount
