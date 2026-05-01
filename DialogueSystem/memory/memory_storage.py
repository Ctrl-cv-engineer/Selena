"""Local persistence helpers for DialogueSystem memory layers."""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime


class PersistentCoreMemoryStore:
    """Stores the persistent core memory subset on disk as JSON.

    Schema v2 adds a top-level `history` list holding entries superseded or
    deleted from `state`, so the time line of core memory edits is preserved.
    Legacy v1 files are still readable; the first save after load upgrades
    them to v2 in place.
    """

    SCHEMA_VERSION = 2

    def __init__(self, file_path: str):
        self.file_path = os.path.abspath(file_path)
        self._lock = threading.RLock()
        directory = os.path.dirname(self.file_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

    def load(self):
        return self.load_payload().get("state") or {}

    def load_history(self):
        return self.load_payload().get("history") or []

    def load_payload(self):
        empty_payload = {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": None,
            "state": {},
            "history": [],
        }
        with self._lock:
            if not os.path.exists(self.file_path):
                return empty_payload
            try:
                with open(self.file_path, "r", encoding="utf-8") as file:
                    payload = json.load(file)
            except Exception:
                return empty_payload
        if not isinstance(payload, dict):
            return empty_payload
        state = payload.get("state")
        if not isinstance(state, dict):
            state = {}
        history = payload.get("history")
        if not isinstance(history, list):
            history = []
        return {
            "schema_version": int(payload.get("schema_version") or 1),
            "updated_at": payload.get("updated_at"),
            "state": state,
            "history": history,
        }

    def save(self, state: dict, history=None):
        """Persist state (+ optional history).

        When `history` is None, the existing on-disk history is preserved so
        legacy callers that only know about state don't accidentally erase it.
        """
        normalized_state = dict(state or {})
        if history is None:
            preserved_history = list(self.load_payload().get("history") or [])
        else:
            preserved_history = list(history or [])
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "updated_at": time.time(),
            "state": normalized_state,
            "history": preserved_history,
        }
        temp_path = self.file_path + ".tmp"
        with self._lock:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(temp_path, self.file_path)


class TopicArchiveRepository:
    """Stores archived topic summaries and raw topic records in SQLite."""

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
                CREATE TABLE IF NOT EXISTS topic_archives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_file TEXT NOT NULL UNIQUE,
                    source_session_prefix TEXT,
                    source_topic_group INTEGER,
                    topic_message_count INTEGER NOT NULL DEFAULT 0,
                    summary_text TEXT NOT NULL,
                    topic_records_json TEXT NOT NULL,
                    archived_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_topic_archives_session_group
                ON topic_archives (source_session_prefix, source_topic_group)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_topic_archives_archived_at
                ON topic_archives (archived_at DESC)
                """
            )
            self._initialize_fts(connection)
            connection.commit()

    def _initialize_fts(self, connection):
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS topic_archives_fts
            USING fts5(summary_text, content='topic_archives', content_rowid='id')
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS topic_archives_ai AFTER INSERT ON topic_archives BEGIN
                INSERT INTO topic_archives_fts(rowid, summary_text) VALUES (new.id, new.summary_text);
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS topic_archives_ad AFTER DELETE ON topic_archives BEGIN
                INSERT INTO topic_archives_fts(topic_archives_fts, rowid, summary_text) VALUES('delete', old.id, old.summary_text);
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS topic_archives_au AFTER UPDATE ON topic_archives BEGIN
                INSERT INTO topic_archives_fts(topic_archives_fts, rowid, summary_text) VALUES('delete', old.id, old.summary_text);
                INSERT INTO topic_archives_fts(rowid, summary_text) VALUES (new.id, new.summary_text);
            END
            """
        )

    @staticmethod
    def _serialize_row(row):
        if row is None:
            return None
        topic_records = []
        raw_records = row["topic_records_json"]
        if raw_records:
            try:
                parsed_records = json.loads(raw_records)
                if isinstance(parsed_records, list):
                    topic_records = parsed_records
            except Exception:
                topic_records = []
        return {
            "archive_id": row["id"],
            "source_file": row["source_file"],
            "source_session_prefix": row["source_session_prefix"],
            "source_topic_group": row["source_topic_group"],
            "topic_message_count": row["topic_message_count"],
            "summary_text": row["summary_text"],
            "topic_records": topic_records,
            "archived_at": row["archived_at"],
            "updated_at": row["updated_at"],
        }

    def upsert_archive(
        self,
        *,
        source_file: str,
        source_session_prefix: str,
        source_topic_group,
        topic_message_count: int,
        summary_text: str,
        topic_records,
        archived_at: float = None,
    ):
        normalized_source_file = str(source_file or "").strip()
        if not normalized_source_file:
            raise ValueError("source_file is required")
        archive_time = float(archived_at or time.time())
        serialized_records = json.dumps(list(topic_records or []), ensure_ascii=False)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO topic_archives (
                    source_file,
                    source_session_prefix,
                    source_topic_group,
                    topic_message_count,
                    summary_text,
                    topic_records_json,
                    archived_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_file) DO UPDATE SET
                    source_session_prefix=excluded.source_session_prefix,
                    source_topic_group=excluded.source_topic_group,
                    topic_message_count=excluded.topic_message_count,
                    summary_text=excluded.summary_text,
                    topic_records_json=excluded.topic_records_json,
                    archived_at=excluded.archived_at,
                    updated_at=excluded.updated_at
                """,
                (
                    normalized_source_file,
                    str(source_session_prefix or "").strip(),
                    int(source_topic_group) if source_topic_group not in (None, "") else None,
                    max(0, int(topic_message_count or 0)),
                    str(summary_text or "").strip(),
                    serialized_records,
                    archive_time,
                    time.time(),
                ),
            )
            connection.commit()

    def get_archive_by_source_file(self, source_file: str):
        normalized_source_file = str(source_file or "").strip()
        if not normalized_source_file:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM topic_archives
                WHERE source_file = ?
                LIMIT 1
                """,
                (normalized_source_file,),
            ).fetchone()
        return self._serialize_row(row)

    def count_archives(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM topic_archives"
            ).fetchone()
        return int((row["total"] if row is not None else 0) or 0)

    def list_recent_archives(self, limit: int = 6):
        normalized_limit = max(1, min(int(limit or 6), 50))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM topic_archives
                ORDER BY archived_at DESC, id DESC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()
        return [self._serialize_row(row) for row in rows]

    def list_archives(self, limit: int = 200):
        normalized_limit = max(1, min(int(limit or 200), 500))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM topic_archives
                ORDER BY archived_at DESC, id DESC
                LIMIT ?
                """,
                (normalized_limit,),
            ).fetchall()
        return [self._serialize_row(row) for row in rows]

    def list_archives_by_date_range(self, since_ts=None, until_ts=None, limit: int = 20):
        """按 archived_at 范围返回话题归档（左闭右开）。

        since_ts/until_ts 为 None 时代表该侧无限制。索引已覆盖 archived_at。
        """
        normalized_limit = max(1, min(int(limit or 20), 50))
        where_clauses = []
        params = []
        if since_ts is not None:
            where_clauses.append("archived_at >= ?")
            params.append(float(since_ts))
        if until_ts is not None:
            where_clauses.append("archived_at < ?")
            params.append(float(until_ts))
        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        params.append(normalized_limit)
        query = (
            "SELECT * FROM topic_archives"
            + where_sql
            + " ORDER BY archived_at DESC, id DESC LIMIT ?"
        )
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._serialize_row(row) for row in rows]

    def get_archive_by_id(self, archive_id):
        try:
            normalized_archive_id = int(archive_id)
        except (TypeError, ValueError):
            return None
        if normalized_archive_id <= 0:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM topic_archives
                WHERE id = ?
                LIMIT 1
                """,
                (normalized_archive_id,),
            ).fetchone()
        return self._serialize_row(row)

    def create_archive(
        self,
        *,
        source_file: str,
        source_session_prefix: str = "",
        source_topic_group=None,
        topic_message_count: int = 0,
        summary_text: str = "",
        topic_records=None,
        archived_at: float = None,
    ):
        normalized_source_file = str(source_file or "").strip()
        if not normalized_source_file:
            raise ValueError("source_file is required")
        normalized_topic_records = list(topic_records or [])
        normalized_archived_at = float(archived_at or time.time())
        try:
            normalized_topic_group = int(source_topic_group) if source_topic_group not in (None, "") else None
        except (TypeError, ValueError):
            normalized_topic_group = None
        normalized_message_count = max(
            0,
            int(topic_message_count if topic_message_count not in (None, "") else len(normalized_topic_records)),
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO topic_archives (
                    source_file,
                    source_session_prefix,
                    source_topic_group,
                    topic_message_count,
                    summary_text,
                    topic_records_json,
                    archived_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_source_file,
                    str(source_session_prefix or "").strip(),
                    normalized_topic_group,
                    normalized_message_count,
                    str(summary_text or "").strip(),
                    json.dumps(normalized_topic_records, ensure_ascii=False),
                    normalized_archived_at,
                    time.time(),
                ),
            )
            archive_id = cursor.lastrowid
            connection.commit()
        return self.get_archive_by_id(archive_id)

    def update_archive(
        self,
        archive_id,
        *,
        source_file: str,
        source_session_prefix: str = "",
        source_topic_group=None,
        topic_message_count: int = 0,
        summary_text: str = "",
        topic_records=None,
        archived_at: float = None,
    ):
        existing_record = self.get_archive_by_id(archive_id)
        if existing_record is None:
            raise ValueError(f"Unknown archive id: {archive_id}")
        normalized_topic_records = list(topic_records or [])
        try:
            normalized_topic_group = int(source_topic_group) if source_topic_group not in (None, "") else None
        except (TypeError, ValueError):
            normalized_topic_group = None
        normalized_archived_at = float(
            archived_at
            if archived_at not in (None, "")
            else existing_record.get("archived_at") or time.time()
        )
        normalized_message_count = max(
            0,
            int(
                topic_message_count
                if topic_message_count not in (None, "")
                else len(normalized_topic_records)
            ),
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE topic_archives
                SET
                    source_file = ?,
                    source_session_prefix = ?,
                    source_topic_group = ?,
                    topic_message_count = ?,
                    summary_text = ?,
                    topic_records_json = ?,
                    archived_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    str(source_file or "").strip(),
                    str(source_session_prefix or "").strip(),
                    normalized_topic_group,
                    normalized_message_count,
                    str(summary_text or "").strip(),
                    json.dumps(normalized_topic_records, ensure_ascii=False),
                    normalized_archived_at,
                    time.time(),
                    int(archive_id),
                ),
            )
            connection.commit()
        return self.get_archive_by_id(archive_id)

    def delete_archives(self, archive_ids):
        normalized_ids = []
        for archive_id in archive_ids or []:
            try:
                normalized_archive_id = int(archive_id)
            except (TypeError, ValueError):
                continue
            if normalized_archive_id > 0:
                normalized_ids.append(normalized_archive_id)
        if not normalized_ids:
            raise ValueError("archive ids must be a non-empty array")
        placeholders = ",".join("?" for _ in normalized_ids)
        with self._lock, self._connect() as connection:
            connection.execute(
                f"DELETE FROM topic_archives WHERE id IN ({placeholders})",
                tuple(normalized_ids),
            )
            connection.commit()
        return normalized_ids

    def search_fulltext(self, query: str, limit: int = 10):
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []
        normalized_limit = max(1, min(int(limit or 10), 50))
        fts_query = " ".join(
            f'"{token}"' for token in normalized_query.split() if token.strip()
        )
        if not fts_query:
            return []
        with self._lock, self._connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT ta.*, rank
                    FROM topic_archives_fts fts
                    JOIN topic_archives ta ON ta.id = fts.rowid
                    WHERE topic_archives_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_query, normalized_limit),
                ).fetchall()
            except Exception:
                rows = connection.execute(
                    """
                    SELECT * FROM topic_archives
                    WHERE summary_text LIKE ?
                    ORDER BY archived_at DESC
                    LIMIT ?
                    """,
                    (f"%{normalized_query}%", normalized_limit),
                ).fetchall()
        return [self._serialize_row(row) for row in rows]

    def rebuild_fts_index(self):
        with self._lock, self._connect() as connection:
            connection.execute("INSERT INTO topic_archives_fts(topic_archives_fts) VALUES('rebuild')")
            connection.commit()


class RetrievalCacheRepository:
    """Stores short-lived agent retrieval results in SQLite."""

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS retrieval_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    query_text TEXT NOT NULL DEFAULT '',
                    raw_result TEXT NOT NULL,
                    summary_text TEXT NOT NULL DEFAULT '',
                    topic_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    expired INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_retrieval_cache_session
                ON retrieval_cache (session_id, expired)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_retrieval_cache_topic
                ON retrieval_cache (topic_id, expired)
                """
            )
            connection.commit()

    @staticmethod
    def _serialize_row(row):
        if row is None:
            return None
        return {
            "id": int(row["id"] or 0),
            "session_id": str(row["session_id"] or "").strip(),
            "tool_name": str(row["tool_name"] or "").strip(),
            "query_text": str(row["query_text"] or "").strip(),
            "raw_result": str(row["raw_result"] or ""),
            "summary_text": str(row["summary_text"] or ""),
            "topic_id": str(row["topic_id"] or "").strip(),
            "created_at": str(row["created_at"] or "").strip(),
            "expired": int(row["expired"] or 0),
        }

    @staticmethod
    def _normalize_ids(cache_ids) -> list[int]:
        normalized_ids = []
        for cache_id in cache_ids or []:
            try:
                parsed_id = int(cache_id)
            except (TypeError, ValueError):
                continue
            if parsed_id > 0 and parsed_id not in normalized_ids:
                normalized_ids.append(parsed_id)
        return normalized_ids

    def clear_all(self):
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM retrieval_cache")
            connection.commit()

    def add_record(
        self,
        *,
        session_id: str,
        tool_name: str,
        query_text: str = "",
        raw_result: str,
        summary_text: str = "",
        topic_id: str = "",
        created_at: str = "",
    ):
        normalized_session_id = str(session_id or "").strip()
        normalized_tool_name = str(tool_name or "").strip()
        normalized_raw_result = str(raw_result or "")
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not normalized_tool_name:
            raise ValueError("tool_name is required")
        if not normalized_raw_result:
            raise ValueError("raw_result is required")
        normalized_created_at = str(created_at or "").strip() or (
            datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO retrieval_cache (
                    session_id,
                    tool_name,
                    query_text,
                    raw_result,
                    summary_text,
                    topic_id,
                    created_at,
                    expired
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    normalized_session_id,
                    normalized_tool_name,
                    str(query_text or "").strip(),
                    normalized_raw_result,
                    str(summary_text or ""),
                    str(topic_id or "").strip(),
                    normalized_created_at,
                ),
            )
            record_id = cursor.lastrowid
            connection.commit()
        return self.get_record_by_id(record_id)

    def get_record_by_id(self, cache_id):
        normalized_ids = self._normalize_ids([cache_id])
        if not normalized_ids:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM retrieval_cache
                WHERE id = ?
                LIMIT 1
                """,
                (normalized_ids[0],),
            ).fetchone()
        return self._serialize_row(row)

    def list_active_records(self, session_id: str, limit: int = 20):
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            return []
        normalized_limit = max(1, min(int(limit or 20), 100))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM retrieval_cache
                WHERE session_id = ? AND expired = 0
                ORDER BY id DESC
                LIMIT ?
                """,
                (normalized_session_id, normalized_limit),
            ).fetchall()
        return [self._serialize_row(row) for row in rows]

    def get_active_records_by_ids(self, session_id: str, cache_ids):
        normalized_session_id = str(session_id or "").strip()
        normalized_ids = self._normalize_ids(cache_ids)
        if not normalized_session_id or not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        params = [normalized_session_id, *normalized_ids]
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM retrieval_cache
                WHERE session_id = ? AND expired = 0 AND id IN ({placeholders})
                """,
                tuple(params),
            ).fetchall()
        row_lookup = {
            int(row["id"] or 0): self._serialize_row(row)
            for row in rows
        }
        return [row_lookup[cache_id] for cache_id in normalized_ids if cache_id in row_lookup]

    def update_summary_text(self, cache_ids, summary_text: str):
        normalized_ids = self._normalize_ids(cache_ids)
        normalized_summary = str(summary_text or "")
        if not normalized_ids or not normalized_summary:
            return 0
        placeholders = ",".join("?" for _ in normalized_ids)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE retrieval_cache
                SET summary_text = ?
                WHERE id IN ({placeholders}) AND expired = 0
                """,
                tuple([normalized_summary, *normalized_ids]),
            )
            connection.commit()
        return int(cursor.rowcount or 0)

    def mark_topic_expired(self, session_id: str, topic_id: str):
        normalized_session_id = str(session_id or "").strip()
        normalized_topic_id = str(topic_id or "").strip()
        if not normalized_session_id or not normalized_topic_id:
            return 0
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE retrieval_cache
                SET expired = 1
                WHERE session_id = ? AND topic_id = ? AND expired = 0
                """,
                (normalized_session_id, normalized_topic_id),
            )
            connection.commit()
        return int(cursor.rowcount or 0)

    def mark_records_expired(self, cache_ids):
        normalized_ids = self._normalize_ids(cache_ids)
        if not normalized_ids:
            return 0
        placeholders = ",".join("?" for _ in normalized_ids)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE retrieval_cache
                SET expired = 1
                WHERE id IN ({placeholders}) AND expired = 0
                """,
                tuple(normalized_ids),
            )
            connection.commit()
        return int(cursor.rowcount or 0)
