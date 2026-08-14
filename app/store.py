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
    apply_diff,
    approval_errors,
    canonical_json,
    code_index_diff,
    validate_document,
    with_code_snapshot,
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
                    review_status TEXT,
                    review_summary TEXT,
                    validation_context_hash TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_run (
                    id TEXT PRIMARY KEY,
                    requirement_id TEXT NOT NULL REFERENCES requirement(id),
                    task TEXT NOT NULL,
                    revision_id TEXT NOT NULL REFERENCES revision(id),
                    implementation_run_id TEXT REFERENCES implementation_run(id),
                    status TEXT NOT NULL,
                    prompt_id TEXT,
                    prompt_version TEXT,
                    prompt_hash TEXT,
                    input_hash TEXT,
                    reply TEXT,
                    focus_node_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tool_invocation (
                    id TEXT PRIMARY KEY,
                    agent_run_id TEXT NOT NULL REFERENCES agent_run(id),
                    implementation_run_id TEXT REFERENCES implementation_run(id),
                    name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_summary TEXT NOT NULL,
                    output_summary TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS run_test_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scenario_id TEXT NOT NULL,
                    test_node_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status = 'PASS'),
                    implementation_run_id TEXT NOT NULL REFERENCES implementation_run(id),
                    revision_id TEXT NOT NULL REFERENCES revision(id),
                    observed_at TEXT NOT NULL,
                    source TEXT NOT NULL CHECK (source = 'OBSERVED'),
                    UNIQUE (implementation_run_id, scenario_id, test_node_id)
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
            run_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(implementation_run)")
            }
            for name in ("review_status", "review_summary", "validation_context_hash"):
                if name not in run_columns:
                    connection.execute(f"ALTER TABLE implementation_run ADD COLUMN {name} TEXT")
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
                if item["status"] == "CANDIDATE":
                    connection.execute(
                        """
                        UPDATE revision
                        SET base_revision_id = ?, content_json = ?, content_hash = ?, approvable = ?
                        WHERE id = ? AND status = 'CANDIDATE'
                        """,
                        (
                            item["baseRevisionId"],
                            canonical_json(document),
                            item["contentHash"],
                            int(bool(item["approvable"])),
                            item["id"],
                        ),
                    )
            connection.execute(
                """
                UPDATE implementation_run
                SET status = 'FAILED', summary = '应用重启，无法确认上次运行结果', updated_at = ?
                WHERE status NOT IN ('COMPLETED', 'FAILED', 'NEEDS_INPUT', 'BLOCKED')
                """,
                (now,),
            )

            connection.execute(
                """
                UPDATE agent_run
                SET status = 'FAILED', reply = '应用重启，无法确认上次 Agent 结果', updated_at = ?
                WHERE status = 'RUNNING'
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE tool_invocation
                SET status = 'FAILED', output_summary = '应用重启，工具调用结果未知', updated_at = ?
                WHERE status = 'RUNNING'
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
            current = connection.execute(
                "SELECT current_revision_id FROM repository WHERE id = ?", (REPOSITORY_ID,)
            ).fetchone()
            current_id = str(current["current_revision_id"]) if current else ""
            ancestor_id: str | None = str(revision["id"])
            visited: set[str] = set()
            while ancestor_id and ancestor_id not in visited:
                if ancestor_id == current_id:
                    status = "REVIEWING" if revision["status"] == "CANDIDATE" else "READY"
                    connection.execute(
                        """
                        UPDATE repository SET current_revision_id = ?, updated_at = ? WHERE id = ?
                        """,
                        (revision["id"], now, REPOSITORY_ID),
                    )
                    connection.execute(
                        """
                        UPDATE requirement
                        SET current_revision_id = ?, status = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (revision["id"], status, now, REQUIREMENT_ID),
                    )
                    break
                visited.add(ancestor_id)
                parent = connection.execute(
                    "SELECT base_revision_id FROM revision WHERE id = ?", (ancestor_id,)
                ).fetchone()
                ancestor_id = str(parent["base_revision_id"]) if parent and parent[0] else None

    def terminal_worktrees(self) -> tuple[tuple[str, Path], ...]:
        """读取应用启动前已经进入终态的受管 worktree。"""
        if not self.path.is_file():
            return ()
        with self._connect() as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'implementation_run'"
            ).fetchone()
            if table is None:
                return ()
            rows = connection.execute(
                """
                SELECT id, worktree FROM implementation_run
                WHERE status IN ('COMPLETED', 'FAILED', 'BLOCKED', 'NEEDS_INPUT')
                  AND worktree IS NOT NULL
                ORDER BY created_at ASC
                """
            )
            return tuple((str(row["id"]), Path(str(row["worktree"]))) for row in rows)

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
                    FROM (
                        SELECT id, role, content, created_at
                        FROM message
                        WHERE requirement_id = ?
                        ORDER BY id DESC
                        LIMIT 200
                    )
                    ORDER BY id ASC
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
            agent_row = connection.execute(
                """
                SELECT * FROM agent_run
                WHERE requirement_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (REQUIREMENT_ID,),
            ).fetchone()
            agent = self._agent_row(agent_row) if agent_row else None
            tools = [
                self._tool_row(row)
                for row in connection.execute(
                    """
                    SELECT * FROM tool_invocation
                    ORDER BY created_at DESC
                    LIMIT 20
                    """
                )
            ]
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
                "agentRun": agent,
                "toolInvocations": tools,
            }

    def begin_agent(self, content: str) -> tuple[str, JsonObject, list[JsonObject]]:
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
                SET operation_status = 'AGENT_RUNNING', status = 'AGENT_RUNNING',
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
                    SELECT role, content
                    FROM (
                        SELECT id, role, content
                        FROM message
                        WHERE requirement_id = ?
                        ORDER BY id DESC
                        LIMIT 200
                    )
                    ORDER BY id ASC
                    """,
                    (REQUIREMENT_ID,),
                )
            ]
            revision = cast(JsonObject, current["revision"])
            agent_run_id = f"AGENT-{uuid.uuid4().hex.upper()}"
            connection.execute(
                """
                INSERT INTO agent_run (
                    id, requirement_id, task, revision_id, status, created_at, updated_at
                ) VALUES (?, ?, 'REQUIREMENT_CHANGE', ?, 'RUNNING', ?, ?)
                """,
                (agent_run_id, REQUIREMENT_ID, revision["id"], now, now),
            )
            return agent_run_id, current, messages

    def begin_baseline_agent(self) -> tuple[str, JsonObject]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            requirement = connection.execute(
                "SELECT * FROM requirement WHERE id = ?", (REQUIREMENT_ID,)
            ).fetchone()
            if requirement is None or requirement["operation_status"] != "IDLE":
                raise StoreError("当前已有 AI 任务正在执行")
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
            revision = cast(JsonObject, current["revision"])
            now = _now()
            agent_run_id = f"AGENT-{uuid.uuid4().hex.upper()}"
            connection.execute(
                """
                INSERT INTO agent_run (
                    id, requirement_id, task, revision_id, status, created_at, updated_at
                ) VALUES (?, ?, 'REPOSITORY_BASELINE', ?, 'RUNNING', ?, ?)
                """,
                (agent_run_id, REQUIREMENT_ID, revision["id"], now, now),
            )
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'AGENT_RUNNING', status = 'AGENT_RUNNING',
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, REQUIREMENT_ID),
            )
            return agent_run_id, current

    def next_revision_id(self) -> str:
        with self._connect() as connection:
            ids = [row[0] for row in connection.execute("SELECT id FROM revision")]
        numbers = [
            int(match.group(1))
            for revision_id in ids
            if (match := re.search(r"-(\d+)$", revision_id))
        ]
        return f"REV-REVIEW-TOOL-{max(numbers, default=0) + 1:03d}"

    def create_candidate(
        self,
        agent_run_id: str,
        diff: JsonObject,
        *,
        base: JsonObject | None = None,
    ) -> JsonObject:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_run WHERE id = ?", (agent_run_id,)
            ).fetchone()
            if (
                row is None
                or row["status"] != "RUNNING"
                or row["task"] not in {"REQUIREMENT_CHANGE", "REPOSITORY_BASELINE"}
            ):
                raise StoreError("Agent 运行不存在或已经结束")
            current_row = connection.execute(
                """
                SELECT revision.* FROM revision
                JOIN repository ON repository.current_revision_id = revision.id
                WHERE repository.id = ?
                """,
                (REPOSITORY_ID,),
            ).fetchone()
            persisted = self._revision_row(current_row)
            current = base or persisted
        revision_id = self.next_revision_id()
        document = apply_diff(current, diff, revision_id, self.graph_schema)
        revision = cast(JsonObject, document["revision"])
        current_revision = cast(JsonObject, persisted["revision"])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            repository = connection.execute(
                "SELECT current_revision_id FROM repository WHERE id = ?", (REPOSITORY_ID,)
            ).fetchone()
            if repository is None or repository["current_revision_id"] != current_revision["id"]:
                raise StoreError("统一图已变化，请重新读取后再提交")
            now = _now()
            connection.execute(
                """
                INSERT INTO revision (
                    id, requirement_id, repository_id, base_revision_id, status, content_json,
                    content_hash, approvable, created_at
                ) VALUES (?, ?, ?, ?, 'CANDIDATE', ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    REQUIREMENT_ID,
                    REPOSITORY_ID,
                    current_revision["id"],
                    canonical_json(document),
                    revision["contentHash"],
                    int(bool(revision["approvable"])),
                    now,
                ),
            )
            connection.execute(
                "UPDATE repository SET current_revision_id = ?, updated_at = ? WHERE id = ?",
                (revision_id, now, REPOSITORY_ID),
            )
            connection.execute(
                """
                UPDATE requirement
                SET current_revision_id = ?, status = 'REVIEWING', last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (revision_id, now, REQUIREMENT_ID),
            )
        return document

    def sync_code_index(self, agent_run_id: str, index: JsonObject) -> JsonObject:
        current = self.current_document()
        snapshot = cast(JsonObject, index["snapshot"])
        base = with_code_snapshot(current, snapshot)
        return self.create_candidate(agent_run_id, code_index_diff(base, index), base=base)

    def record_agent_prompt(
        self,
        agent_run_id: str,
        prompt_id: str,
        prompt_version: str,
        prompt_hash: str,
        input_hash: str,
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_run
                SET prompt_id = ?, prompt_version = ?, prompt_hash = ?,
                    input_hash = ?, updated_at = ?
                WHERE id = ? AND status = 'RUNNING'
                """,
                (
                    prompt_id,
                    prompt_version,
                    prompt_hash,
                    input_hash,
                    _now(),
                    agent_run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise StoreError("Agent 运行不存在或已经结束")

    def finish_agent(self, agent_run_id: str, result: JsonObject) -> None:
        status = result.get("status")
        reply = result.get("reply")
        focus_node_ids = result.get("focusNodeIds")
        if status not in {
            "COMPLETED",
            "NEEDS_INPUT",
            "AWAITING_APPROVAL",
            "BLOCKED",
            "FAILED",
        }:
            raise StoreError("Agent 返回了未知状态")
        if not isinstance(reply, str) or not isinstance(focus_node_ids, list):
            raise StoreError("Agent 结果格式无效")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM agent_run WHERE id = ?", (agent_run_id,)
            ).fetchone()
            if row is None or row["status"] != "RUNNING":
                raise StoreError("Agent 运行不存在或已经结束")
            now = _now()
            connection.execute(
                """
                UPDATE agent_run
                SET status = ?, reply = ?, focus_node_ids_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, reply.strip(), json.dumps(focus_node_ids), now, agent_run_id),
            )
            connection.execute(
                """
                INSERT INTO message (requirement_id, role, content, created_at)
                VALUES (?, 'AI', ?, ?)
                """,
                (REQUIREMENT_ID, reply.strip(), now),
            )
            implementation_run_id = row["implementation_run_id"]
            if implementation_run_id is None:
                current = connection.execute(
                    """
                    SELECT revision.status FROM revision
                    JOIN repository ON repository.current_revision_id = revision.id
                    WHERE repository.id = ?
                    """,
                    (REPOSITORY_ID,),
                ).fetchone()
                candidate = current is not None and current["status"] == "CANDIDATE"
                requirement_status = (
                    "REVIEWING" if status == "AWAITING_APPROVAL" and candidate else status
                )
                if status == "COMPLETED":
                    requirement_status = "REVIEWING" if candidate else "READY"
                connection.execute(
                    """
                    UPDATE requirement
                    SET operation_status = 'IDLE', status = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        requirement_status,
                        reply if status == "FAILED" else None,
                        now,
                        REQUIREMENT_ID,
                    ),
                )
            elif status in {"NEEDS_INPUT", "BLOCKED", "FAILED"}:
                connection.execute(
                    """
                    UPDATE implementation_run
                    SET status = ?, summary = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        reply,
                        now,
                        implementation_run_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE requirement
                    SET operation_status = 'IDLE', status = ?, last_error = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        reply if status == "FAILED" else None,
                        now,
                        REQUIREMENT_ID,
                    ),
                )

    def fail_agent(self, agent_run_id: str, error: str) -> None:
        self.finish_agent(
            agent_run_id,
            {"status": "FAILED", "reply": error.strip() or "未知错误", "focusNodeIds": []},
        )

    def approve_and_create_run(
        self, revision_id: str, content_hash: str
    ) -> tuple[str, str, JsonObject]:
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
            agent_run_id = f"AGENT-{uuid.uuid4().hex.upper()}"
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
                INSERT INTO agent_run (
                    id, requirement_id, task, revision_id, implementation_run_id,
                    status, created_at, updated_at
                ) VALUES (?, ?, 'IMPLEMENTATION', ?, ?, 'RUNNING', ?, ?)
                """,
                (agent_run_id, REQUIREMENT_ID, revision_id, run_id, now, now),
            )
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'AGENT_RUNNING', status = 'DEVELOPING', updated_at = ?
                WHERE id = ?
                """,
                (now, REQUIREMENT_ID),
            )
            return run_id, agent_run_id, cast(JsonObject, approved)

    def retry_delivery(self, revision_id: str, content_hash: str) -> tuple[str, str, JsonObject]:
        """为当前批准 revision 的最新失败运行创建独立重试。"""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            requirement = connection.execute(
                "SELECT * FROM requirement WHERE id = ?", (REQUIREMENT_ID,)
            ).fetchone()
            if requirement is None or requirement["operation_status"] != "IDLE":
                raise StoreError("当前需求操作未空闲，不能重新自动交付")
            repository = connection.execute(
                "SELECT current_revision_id FROM repository WHERE id = ?", (REPOSITORY_ID,)
            ).fetchone()
            if repository is None or repository["current_revision_id"] != revision_id:
                raise StoreError("页面 revision 已过期，请刷新后重试")
            row = connection.execute(
                "SELECT * FROM revision WHERE id = ?", (revision_id,)
            ).fetchone()
            if row is None or row["status"] != "APPROVED" or row["content_hash"] != content_hash:
                raise StoreError("批准 revision ID 或内容哈希不匹配")
            latest = connection.execute(
                """
                SELECT * FROM implementation_run
                WHERE requirement_id = ? AND revision_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (REQUIREMENT_ID, revision_id),
            ).fetchone()
            if latest is None or latest["status"] != "FAILED":
                raise StoreError("仅可重试当前批准 revision 的最新失败运行")

            document = self._revision_row(row)
            validate_document(document, self.graph_schema)
            run_id = f"RUN-{uuid.uuid4().hex.upper()}"
            agent_run_id = f"AGENT-{uuid.uuid4().hex.upper()}"
            now = _now()
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
                INSERT INTO agent_run (
                    id, requirement_id, task, revision_id, implementation_run_id,
                    status, created_at, updated_at
                ) VALUES (?, ?, 'IMPLEMENTATION', ?, ?, 'RUNNING', ?, ?)
                """,
                (agent_run_id, REQUIREMENT_ID, revision_id, run_id, now, now),
            )
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'AGENT_RUNNING', status = 'DEVELOPING',
                    last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, REQUIREMENT_ID),
            )
            return run_id, agent_run_id, document

    def begin_review_agent(self, run_id: str) -> tuple[str, JsonObject]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT * FROM implementation_run WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None or run["status"] != "VERIFIED":
                raise StoreError("固定测试尚未通过，不能开始语义 Review")
            document = self._revision_row(
                connection.execute(
                    "SELECT * FROM revision WHERE id = ?", (run["revision_id"],)
                ).fetchone()
            )
            agent_run_id = f"AGENT-{uuid.uuid4().hex.upper()}"
            now = _now()
            connection.execute(
                """
                INSERT INTO agent_run (
                    id, requirement_id, task, revision_id, implementation_run_id,
                    status, created_at, updated_at
                ) VALUES (?, ?, 'SEMANTIC_REVIEW', ?, ?, 'RUNNING', ?, ?)
                """,
                (agent_run_id, REQUIREMENT_ID, run["revision_id"], run_id, now, now),
            )
            connection.execute(
                "UPDATE implementation_run SET status = 'REVIEWING', updated_at = ? WHERE id = ?",
                (now, run_id),
            )
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'AGENT_RUNNING', status = 'REVIEWING', updated_at = ?
                WHERE id = ?
                """,
                (now, REQUIREMENT_ID),
            )
            return agent_run_id, document

    def agent(self, agent_run_id: str) -> JsonObject:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_run WHERE id = ?", (agent_run_id,)
            ).fetchone()
        if row is None:
            raise StoreError("Agent 运行不存在")
        return self._agent_row(row)

    def implementation_run(self, run_id: str) -> JsonObject:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM implementation_run WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise StoreError("开发运行不存在")
        return self._run_row(row)

    def run_revision_documents(self, run_id: str) -> tuple[JsonObject | None, JsonObject]:
        """按 implementation run 固定读取批准 revision 及其直接父 revision。"""
        with self._connect() as connection:
            run = connection.execute(
                "SELECT revision_id FROM implementation_run WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise StoreError("开发运行不存在")
            current_row = connection.execute(
                "SELECT * FROM revision WHERE id = ?", (run["revision_id"],)
            ).fetchone()
            current = self._revision_row(current_row)
            revision = cast(JsonObject, current["revision"])
            base_id = revision.get("baseRevisionId")
            base_row = (
                connection.execute("SELECT * FROM revision WHERE id = ?", (base_id,)).fetchone()
                if isinstance(base_id, str)
                else None
            )
            return (self._revision_row(base_row) if base_row is not None else None, current)

    def current_document(self) -> JsonObject:
        return cast(JsonObject, self.state()["revision"])

    def start_run(self, run_id: str, worktree: Path) -> None:
        run = self.implementation_run(run_id)
        if run["status"] != "PENDING":
            raise StoreError("当前开发运行不能创建 worktree")
        self._update_run(run_id, "RUNNING", worktree=str(worktree))

    def submit_change(self, run_id: str, summary: str) -> None:
        run = self.implementation_run(run_id)
        if run["status"] not in {"RUNNING", "TESTING"}:
            raise StoreError("当前开发运行不能提交变更摘要")
        self._update_run(run_id, "RUNNING", summary=summary.strip())

    def begin_test(self, run_id: str) -> Path:
        run = self.implementation_run(run_id)
        if run["status"] not in {"RUNNING", "VERIFIED"}:
            raise StoreError("当前开发运行不能执行固定测试")
        worktree = run.get("worktree")
        if not isinstance(worktree, str):
            raise StoreError("开发 worktree 尚未创建")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM run_test_evidence WHERE implementation_run_id = ?", (run_id,)
            )
            connection.execute(
                """
                UPDATE implementation_run
                SET status = 'TESTING', summary = '正在执行项目固定测试', updated_at = ?
                WHERE id = ?
                """,
                (_now(), run_id),
            )
        return Path(worktree)

    def finish_test(
        self,
        run_id: str,
        passed: bool,
        summary: str,
        validation_context_hash: str,
        evidence: tuple[JsonObject, ...] = (),
    ) -> None:
        run = self.implementation_run(run_id)
        if run["status"] != "TESTING":
            raise StoreError("固定测试状态已变化")
        if passed and not validation_context_hash:
            raise StoreError("验证上下文哈希不能为空")
        with self._connect() as connection:
            if passed and evidence:
                observed_at = _now()
                connection.execute(
                    "DELETE FROM run_test_evidence WHERE implementation_run_id = ?", (run_id,)
                )
                connection.executemany(
                    """
                    INSERT INTO run_test_evidence (
                        scenario_id, test_node_id, status, implementation_run_id,
                        revision_id, observed_at, source
                    ) VALUES (?, ?, 'PASS', ?, ?, ?, 'OBSERVED')
                    """,
                    [
                        (
                            str(item["scenarioId"]),
                            str(item["testNodeId"]),
                            run_id,
                            str(run["revisionId"]),
                            observed_at,
                        )
                        for item in evidence
                    ],
                )
            connection.execute(
                """
                UPDATE implementation_run
                SET status = ?, summary = ?, validation_context_hash = ?, updated_at = ?
                WHERE id = ? AND status = 'TESTING'
                """,
                (
                    "VERIFIED" if passed else "RUNNING",
                    summary,
                    validation_context_hash if passed else None,
                    _now(),
                    run_id,
                ),
            )

    def latest_run_test_evidence(self, revision_id: str) -> JsonObject:
        """读取指定 revision 最新运行及该运行自身的逐测试证据。"""
        with self._connect() as connection:
            run = connection.execute(
                """
                SELECT * FROM implementation_run
                WHERE revision_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (revision_id,),
            ).fetchone()
            if run is None:
                return {"run": None, "evidence": []}
            rows = connection.execute(
                """
                SELECT scenario_id, test_node_id, status, implementation_run_id,
                       revision_id, observed_at, source
                FROM run_test_evidence
                WHERE implementation_run_id = ?
                ORDER BY scenario_id, test_node_id
                """,
                (run["id"],),
            )
            return {
                "run": self._run_row(run),
                "evidence": [
                    {
                        "scenarioId": row["scenario_id"],
                        "testNodeId": row["test_node_id"],
                        "status": row["status"],
                        "implementationRunId": row["implementation_run_id"],
                        "revisionId": row["revision_id"],
                        "observedAt": row["observed_at"],
                        "source": row["source"],
                    }
                    for row in rows
                ],
            }

    def submit_review(self, run_id: str, status: str, summary: str) -> None:
        if status not in {"PASS", "BLOCKED"}:
            raise StoreError("语义 Review 状态必须是 PASS 或 BLOCKED")
        run = self.implementation_run(run_id)
        if run["status"] != "REVIEWING":
            raise StoreError("当前开发运行不能提交语义 Review")
        with self._connect() as connection:
            now = _now()
            next_status = "REVIEW_PASSED" if status == "PASS" else "BLOCKED"
            connection.execute(
                """
                UPDATE implementation_run
                SET status = ?, review_status = ?, review_summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (next_status, status, summary.strip(), now, run_id),
            )
            if status == "BLOCKED":
                connection.execute(
                    """
                    UPDATE requirement
                    SET operation_status = 'IDLE', status = 'BLOCKED', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, REQUIREMENT_ID),
                )

    def begin_merge(self, run_id: str) -> tuple[Path, str]:
        run = self.implementation_run(run_id)
        worktree = run.get("worktree")
        revision_id = run.get("revisionId")
        if (
            run["status"] != "REVIEW_PASSED"
            or run.get("reviewStatus") != "PASS"
            or not isinstance(worktree, str)
            or not isinstance(revision_id, str)
        ):
            raise StoreError("固定测试和语义 Review 未通过，不能合并")
        self._update_run(run_id, "MERGING", summary="正在安全合并本地分支")
        return Path(worktree), revision_id

    def begin_merge_verified(self, run_id: str, validation_context_hash: str) -> tuple[Path, str]:
        """原子核验测试、Review 与固定变化上下文后进入合并。"""
        if not validation_context_hash:
            raise StoreError("验证上下文哈希不能为空")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT * FROM implementation_run WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise StoreError("开发运行不存在")
            if (
                run["status"] != "REVIEW_PASSED"
                or run["review_status"] != "PASS"
                or not run["validation_context_hash"]
                or run["validation_context_hash"] != validation_context_hash
                or not run["worktree"]
                or not run["revision_id"]
            ):
                raise StoreError("固定测试、语义 Review 或验证上下文不匹配，不能合并")
            cursor = connection.execute(
                """
                UPDATE implementation_run
                SET status = 'MERGING', summary = ?, updated_at = ?
                WHERE id = ? AND status = 'REVIEW_PASSED'
                """,
                ("正在安全合并本地分支", _now(), run_id),
            )
            if cursor.rowcount != 1:
                raise StoreError("开发运行状态已变化，不能合并")
            return Path(str(run["worktree"])), str(run["revision_id"])

    def complete_delivery(self, run_id: str, summary: str) -> None:
        run = self.implementation_run(run_id)
        if run["status"] != "MERGING":
            raise StoreError("开发运行不在合并状态")
        self._update_run(run_id, "COMPLETED", summary=summary)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'IDLE', status = 'READY', last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (_now(), REQUIREMENT_ID),
            )

    def begin_tool(
        self,
        agent_run_id: str,
        implementation_run_id: str | None,
        name: str,
        input_summary: str,
    ) -> str:
        tool_id = f"TOOL-{uuid.uuid4().hex.upper()}"
        with self._connect() as connection:
            agent = connection.execute(
                "SELECT * FROM agent_run WHERE id = ?", (agent_run_id,)
            ).fetchone()
            if agent is None or agent["status"] != "RUNNING":
                raise StoreError("Agent 运行不存在或已经结束")
            if agent["implementation_run_id"] != implementation_run_id:
                raise StoreError("工具调用与开发运行不匹配")
            now = _now()
            connection.execute(
                """
                INSERT INTO tool_invocation (
                    id, agent_run_id, implementation_run_id, name, status,
                    input_summary, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, ?)
                """,
                (
                    tool_id,
                    agent_run_id,
                    implementation_run_id,
                    name,
                    input_summary[:1000],
                    now,
                    now,
                ),
            )
        return tool_id

    def finish_tool(self, tool_id: str, status: str, output_summary: str) -> None:
        if status not in {"COMPLETED", "FAILED"}:
            raise StoreError("未知工具调用状态")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE tool_invocation
                SET status = ?, output_summary = ?, updated_at = ?
                WHERE id = ? AND status = 'RUNNING'
                """,
                (status, output_summary[:2000], _now(), tool_id),
            )
            if cursor.rowcount != 1:
                raise StoreError("工具调用不存在或已经结束")

    def tool_invocations(self, run_id: str) -> list[JsonObject]:
        with self._connect() as connection:
            return [
                self._tool_row(row)
                for row in connection.execute(
                    """
                    SELECT * FROM tool_invocation
                    WHERE implementation_run_id = ?
                    ORDER BY created_at ASC
                    """,
                    (run_id,),
                )
            ]

    def fail_run(self, run_id: str, error: str) -> None:
        message = error.strip() or "未知错误"
        self._update_run(run_id, "FAILED", summary=message)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE requirement
                SET operation_status = 'IDLE', status = 'FAILED', last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (message, _now(), REQUIREMENT_ID),
            )

    def _update_run(
        self,
        run_id: str,
        status: str,
        *,
        worktree: str | None = None,
        summary: str | None = None,
    ) -> None:
        fields = ["status = ?", "updated_at = ?"]
        values: list[str | None] = [status, _now()]
        for name, value in (("worktree", worktree), ("summary", summary)):
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
            "reviewStatus": row["review_status"],
            "reviewSummary": row["review_summary"],
            "validationContextHash": row["validation_context_hash"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _agent_row(row: sqlite3.Row) -> JsonObject:
        focus_node_ids = json.loads(row["focus_node_ids_json"])
        return {
            "id": row["id"],
            "task": row["task"],
            "revisionId": row["revision_id"],
            "implementationRunId": row["implementation_run_id"],
            "status": row["status"],
            "promptId": row["prompt_id"],
            "promptVersion": row["prompt_version"],
            "reply": row["reply"],
            "focusNodeIds": focus_node_ids if isinstance(focus_node_ids, list) else [],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _tool_row(row: sqlite3.Row) -> JsonObject:
        return {
            "id": row["id"],
            "agentRunId": row["agent_run_id"],
            "implementationRunId": row["implementation_run_id"],
            "name": row["name"],
            "status": row["status"],
            "inputSummary": row["input_summary"],
            "outputSummary": row["output_summary"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
