from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from app.graph import (
    GraphError,
    JsonObject,
    approval_errors,
    canonical_json,
    validate_document,
)

REQUIREMENT_ID = "REQ-REVIEW-TOOL"
REPOSITORY_ID = "REPOSITORY-LOCAL"


class StoreError(RuntimeError):
    """持久化状态不允许当前操作。"""


class Store:
    def __init__(self, path: Path, graph_schema: JsonObject) -> None:
        self.path = path
        self.graph_schema = graph_schema

    def initialize(self, seed: JsonObject, history: tuple[JsonObject, ...] = ()) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for document in (*history, seed):
            validate_document(document, self.graph_schema)
        revision = cast(JsonObject, seed["revision"])
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS requirement (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_revision_id TEXT NOT NULL,
                    operation_status TEXT NOT NULL DEFAULT 'IDLE',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS repository (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    current_revision_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS message (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    requirement_id TEXT NOT NULL REFERENCES requirement(id),
                    role TEXT NOT NULL CHECK (role IN ('USER', 'AI')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS revision (
                    id TEXT PRIMARY KEY,
                    requirement_id TEXT NOT NULL REFERENCES requirement(id),
                    repository_id TEXT NOT NULL DEFAULT 'REPOSITORY-LOCAL',
                    base_revision_id TEXT,
                    status TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    approvable INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS implementation_run (
                    id TEXT PRIMARY KEY,
                    requirement_id TEXT NOT NULL REFERENCES requirement(id),
                    revision_id TEXT NOT NULL REFERENCES revision(id),
                    status TEXT NOT NULL,
                    worktree TEXT,
                    summary TEXT,
                    question TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TRIGGER IF NOT EXISTS approved_revision_content_immutable
                BEFORE UPDATE OF content_json, content_hash ON revision
                WHEN OLD.status = 'APPROVED'
                BEGIN
                    SELECT RAISE(ABORT, '已批准 revision 的模型正文和哈希不可修改');
                END;

                CREATE TRIGGER IF NOT EXISTS approved_revision_not_deletable
                BEFORE DELETE ON revision
                WHEN OLD.status = 'APPROVED'
                BEGIN
                    SELECT RAISE(ABORT, '已批准 revision 不可删除');
                END;
                """
            )
            revision_columns = {row[1] for row in connection.execute("PRAGMA table_info(revision)")}
            if "repository_id" not in revision_columns:
                connection.execute(
                    "ALTER TABLE revision ADD COLUMN repository_id TEXT "
                    f"NOT NULL DEFAULT '{REPOSITORY_ID}'"
                )
            now = _now()
            connection.execute(
                """
                INSERT OR IGNORE INTO requirement (
                    id, title, status, current_revision_id, operation_status, created_at, updated_at
                ) VALUES (?, ?, 'READY', ?, 'IDLE', ?, ?)
                """,
                (REQUIREMENT_ID, seed["title"], revision["id"], now, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO repository (
                    id, title, current_revision_id, created_at, updated_at
                )
                SELECT ?, title, current_revision_id, ?, ?
                FROM requirement WHERE id = ?
                """,
                (REPOSITORY_ID, now, now, REQUIREMENT_ID),
            )
            for document in (*history, seed):
                item = cast(JsonObject, document["revision"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO revision (
                        id, requirement_id, repository_id, base_revision_id, status, content_json,
                        content_hash, approvable, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["id"],
                        REQUIREMENT_ID,
                        REPOSITORY_ID,
                        item["baseRevisionId"],
                        item["status"],
                        canonical_json(document),
                        item["contentHash"],
                        int(bool(item["approvable"])),
                        now,
                    ),
                )
            connection.execute(
                """
                UPDATE implementation_run
                SET status = 'FAILED', summary = '应用重启，无法确认上次运行结果', updated_at = ?
                WHERE status IN ('PENDING', 'RUNNING')
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'IDLE', status = 'READY', updated_at = ?
                WHERE operation_status != 'IDLE'
                """,
                (now,),
            )

    def state(self) -> JsonObject:
        with self._connect() as connection:
            requirement = connection.execute(
                "SELECT * FROM requirement WHERE id = ?", (REQUIREMENT_ID,)
            ).fetchone()
            if requirement is None:
                raise StoreError("需求尚未初始化")
            repository = connection.execute(
                "SELECT * FROM repository WHERE id = ?", (REPOSITORY_ID,)
            ).fetchone()
            if repository is None:
                raise StoreError("仓库尚未初始化")
            current = self._revision_row(
                connection.execute(
                    "SELECT * FROM revision WHERE id = ?", (repository["current_revision_id"],)
                ).fetchone()
            )
            base = None
            current_revision = cast(JsonObject, current["revision"])
            base_id = current_revision.get("baseRevisionId")
            if isinstance(base_id, str):
                row = connection.execute(
                    "SELECT * FROM revision WHERE id = ?", (base_id,)
                ).fetchone()
                base = self._revision_row(row) if row else None
            messages = [
                {
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "createdAt": row["created_at"],
                }
                for row in connection.execute(
                    """
                    SELECT id, role, content, created_at
                    FROM message
                    WHERE requirement_id = ?
                    ORDER BY id ASC
                    LIMIT 200
                    """,
                    (REQUIREMENT_ID,),
                )
            ]
            run_row = connection.execute(
                """
                SELECT * FROM implementation_run
                WHERE requirement_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (REQUIREMENT_ID,),
            ).fetchone()
            run = self._run_row(run_row) if run_row else None
            return {
                "requirement": {
                    "id": requirement["id"],
                    "title": requirement["title"],
                    "status": requirement["status"],
                    "operationStatus": requirement["operation_status"],
                    "lastError": requirement["last_error"],
                },
                "repository": {"id": repository["id"], "title": repository["title"]},
                "revision": current,
                "baseRevision": base,
                "messages": messages,
                "implementationRun": run,
            }

    def begin_modeling(self, content: str) -> tuple[JsonObject, list[JsonObject]]:
        message = content.strip()
        if not message:
            raise StoreError("需求消息不能为空")
        if len(message) > 4000:
            raise StoreError("需求消息不能超过 4000 个字符")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            requirement = connection.execute(
                "SELECT * FROM requirement WHERE id = ?", (REQUIREMENT_ID,)
            ).fetchone()
            if requirement is None or requirement["operation_status"] != "IDLE":
                raise StoreError("当前已有 AI 任务正在执行")
            now = _now()
            connection.execute(
                """
                INSERT INTO message (requirement_id, role, content, created_at)
                VALUES (?, 'USER', ?, ?)
                """,
                (REQUIREMENT_ID, message, now),
            )
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'MODELING', status = 'MODELING',
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, REQUIREMENT_ID),
            )
            current = self._revision_row(
                connection.execute(
                    """
                    SELECT revision.* FROM revision
                    JOIN repository ON repository.current_revision_id = revision.id
                    WHERE repository.id = ?
                    """,
                    (REPOSITORY_ID,),
                ).fetchone()
            )
            messages = [
                {"role": row["role"], "content": row["content"]}
                for row in connection.execute(
                    """
                    SELECT role, content FROM message
                    WHERE requirement_id = ? ORDER BY id ASC LIMIT 200
                    """,
                    (REQUIREMENT_ID,),
                )
            ]
            return current, messages

    def next_revision_id(self) -> str:
        with self._connect() as connection:
            ids = [row[0] for row in connection.execute("SELECT id FROM revision")]
        numbers = [
            int(match.group(1))
            for revision_id in ids
            if (match := re.search(r"-(\d+)$", revision_id))
        ]
        return f"REV-REVIEW-TOOL-{max(numbers, default=0) + 1:03d}"

    def complete_modeling(self, base_revision_id: str, document: JsonObject, reply: str) -> None:
        validate_document(document, self.graph_schema)
        revision = cast(JsonObject, document["revision"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            requirement = connection.execute(
                "SELECT * FROM requirement WHERE id = ?", (REQUIREMENT_ID,)
            ).fetchone()
            if (
                requirement is None
                or requirement["operation_status"] != "MODELING"
                or requirement["current_revision_id"] != base_revision_id
            ):
                raise StoreError("建模结果对应的需求状态已变化")
            now = _now()
            connection.execute(
                """
                INSERT INTO revision (
                    id, requirement_id, repository_id, base_revision_id, status, content_json,
                    content_hash, approvable, created_at
                ) VALUES (?, ?, ?, ?, 'CANDIDATE', ?, ?, ?, ?)
                """,
                (
                    revision["id"],
                    REQUIREMENT_ID,
                    REPOSITORY_ID,
                    base_revision_id,
                    canonical_json(document),
                    revision["contentHash"],
                    int(bool(revision["approvable"])),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO message (requirement_id, role, content, created_at)
                VALUES (?, 'AI', ?, ?)
                """,
                (REQUIREMENT_ID, reply.strip(), now),
            )
            connection.execute(
                """
                UPDATE requirement
                SET current_revision_id = ?, operation_status = 'IDLE', status = 'REVIEWING',
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (revision["id"], now, REQUIREMENT_ID),
            )
            connection.execute(
                """
                UPDATE repository SET current_revision_id = ?, updated_at = ? WHERE id = ?
                """,
                (revision["id"], now, REPOSITORY_ID),
            )

    def fail_modeling(self, error: str) -> None:
        message = error.strip() or "未知错误"
        with self._connect() as connection:
            now = _now()
            connection.execute(
                """
                INSERT INTO message (requirement_id, role, content, created_at)
                VALUES (?, 'AI', ?, ?)
                """,
                (REQUIREMENT_ID, f"建模失败：{message}", now),
            )
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'IDLE', status = 'ERROR', last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (message, now, REQUIREMENT_ID),
            )

    def approve_and_create_run(self, revision_id: str, content_hash: str) -> tuple[str, JsonObject]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            requirement = connection.execute(
                "SELECT * FROM requirement WHERE id = ?", (REQUIREMENT_ID,)
            ).fetchone()
            if requirement is None or requirement["operation_status"] != "IDLE":
                raise StoreError("当前状态不能批准")
            repository = connection.execute(
                "SELECT current_revision_id FROM repository WHERE id = ?", (REPOSITORY_ID,)
            ).fetchone()
            if repository is None or repository["current_revision_id"] != revision_id:
                raise StoreError("页面 revision 已过期，请刷新后重试")
            row = connection.execute(
                "SELECT * FROM revision WHERE id = ?", (revision_id,)
            ).fetchone()
            if row is None or row["status"] != "CANDIDATE" or row["content_hash"] != content_hash:
                raise StoreError("revision ID 或内容哈希不匹配")
            document = self._revision_row(row)
            validate_document(document, self.graph_schema)
            errors = approval_errors(document)
            if not row["approvable"] or errors:
                raise StoreError("；".join(errors) if errors else "当前图不可批准")

            approved = json.loads(canonical_json(document))
            approved_revision = cast(JsonObject, cast(JsonObject, approved)["revision"])
            approved_revision["status"] = "APPROVED"
            approved_revision["approvable"] = False
            run_id = f"RUN-{uuid.uuid4().hex.upper()}"
            now = _now()
            connection.execute(
                """
                UPDATE revision
                SET status = 'APPROVED', content_json = ?, approvable = 0
                WHERE id = ?
                """,
                (canonical_json(cast(JsonObject, approved)), revision_id),
            )
            connection.execute(
                """
                INSERT INTO implementation_run (
                    id, requirement_id, revision_id, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'PENDING', ?, ?)
                """,
                (run_id, REQUIREMENT_ID, revision_id, now, now),
            )
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'DEVELOPING', status = 'DEVELOPING', updated_at = ?
                WHERE id = ?
                """,
                (now, REQUIREMENT_ID),
            )
            return run_id, cast(JsonObject, approved)

    def start_run(self, run_id: str, worktree: Path) -> None:
        self._update_run(run_id, "RUNNING", worktree=str(worktree))

    def update_run_progress(self, run_id: str, status: str, summary: str) -> None:
        if status not in {"VERIFYING", "MERGING"}:
            raise StoreError("未知自动交付状态")
        self._update_run(run_id, status, summary=summary)

    def finish_run(self, run_id: str, result: JsonObject) -> None:
        status = result.get("status")
        if status not in {"COMPLETED", "NEEDS_INPUT", "FAILED"}:
            raise StoreError("Codex 返回了未知开发状态")
        summary = result.get("summary")
        question = result.get("question")
        if not isinstance(summary, str) or not isinstance(question, str):
            raise StoreError("Codex 开发结果格式无效")
        self._update_run(run_id, status, summary=summary, question=question or None)
        with self._connect() as connection:
            now = _now()
            requirement_status = "READY" if status == "COMPLETED" else status
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'IDLE', status = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (requirement_status, summary if status == "FAILED" else None, now, REQUIREMENT_ID),
            )
            if status == "NEEDS_INPUT" and question:
                connection.execute(
                    """
                    INSERT INTO message (requirement_id, role, content, created_at)
                    VALUES (?, 'AI', ?, ?)
                    """,
                    (REQUIREMENT_ID, question, now),
                )

    def fail_run(self, run_id: str, error: str) -> None:
        self.finish_run(
            run_id,
            {"status": "FAILED", "summary": error.strip() or "未知错误", "question": ""},
        )

    def _update_run(
        self,
        run_id: str,
        status: str,
        *,
        worktree: str | None = None,
        summary: str | None = None,
        question: str | None = None,
    ) -> None:
        fields = ["status = ?", "updated_at = ?"]
        values: list[str | None] = [status, _now()]
        for name, value in (("worktree", worktree), ("summary", summary), ("question", question)):
            if value is not None:
                fields.append(f"{name} = ?")
                values.append(value)
        values.append(run_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE implementation_run SET {', '.join(fields)} WHERE id = ?", values
            )
            if cursor.rowcount != 1:
                raise StoreError("开发运行不存在")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _revision_row(row: sqlite3.Row | None) -> JsonObject:
        if row is None:
            raise StoreError("revision 不存在")
        value = json.loads(row["content_json"])
        if not isinstance(value, dict):
            raise GraphError("revision 正文必须是 JSON 对象")
        return cast(JsonObject, value)

    @staticmethod
    def _run_row(row: sqlite3.Row) -> JsonObject:
        return {
            "id": row["id"],
            "revisionId": row["revision_id"],
            "status": row["status"],
            "worktree": row["worktree"],
            "summary": row["summary"],
            "question": row["question"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
