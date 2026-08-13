from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.config import ROOT
from app.graph import JsonObject, load_object
from app.http import create_app
from app.store import REQUIREMENT_ID, Store, StoreError


def _approved_store(tmp_path: Path) -> tuple[Store, JsonObject]:
    document = load_object(ROOT / "model" / "review-tool.json")
    revision = document["revision"]
    revision["status"] = "APPROVED"
    revision["approvable"] = False
    store = Store(tmp_path / "review.sqlite3", load_object(ROOT / "model" / "graph.schema.json"))
    store.initialize(document)
    with sqlite3.connect(store.path) as connection:
        now = "2026-08-14T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO implementation_run (
                id, requirement_id, revision_id, status, worktree, summary,
                validation_context_hash, created_at, updated_at
            ) VALUES ('RUN-FAILED', ?, ?, 'FAILED', '/tmp/旧-worktree', '旧失败', '旧证据', ?, ?)
            """,
            (REQUIREMENT_ID, revision["id"], now, now),
        )
    return store, document


def test_SCN_DELIVERY_RETRY_001_失败后创建全新运行并保留旧审计(tmp_path: Path) -> None:
    store, document = _approved_store(tmp_path)
    revision = document["revision"]

    run_id, _, fixed_document = store.retry_delivery(
        str(revision["id"]), str(revision["contentHash"])
    )

    assert run_id != "RUN-FAILED"
    assert fixed_document["revision"]["id"] == revision["id"]
    assert store.implementation_run("RUN-FAILED")["status"] == "FAILED"
    new_run = store.implementation_run(run_id)
    assert new_run["status"] == "PENDING"
    assert new_run["worktree"] is None
    assert new_run["validationContextHash"] is None


def test_SCN_DELIVERY_RETRY_001_只接受当前批准版本的最新失败运行(tmp_path: Path) -> None:
    store, document = _approved_store(tmp_path)
    revision = document["revision"]
    store.retry_delivery(str(revision["id"]), str(revision["contentHash"]))

    with pytest.raises(StoreError, match="未空闲"):
        store.retry_delivery(str(revision["id"]), str(revision["contentHash"]))


def test_SCN_DELIVERY_RETRY_001_HTTP要求用户明确确认(tmp_path: Path) -> None:
    class _服务:
        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def retry_delivery(self, revision_id: str, content_hash: str) -> str:
            assert revision_id == "REV-1"
            assert content_hash == "hash"
            return "RUN-NEW"

    with TestClient(create_app(_服务(), tmp_path)) as client:  # type: ignore[arg-type]
        rejected = client.post("/api/revision/REV-1/retry-delivery", json={"contentHash": "hash"})
        accepted = client.post(
            "/api/revision/REV-1/retry-delivery",
            json={"contentHash": "hash", "confirmed": True},
        )

    assert rejected.status_code == 409
    assert accepted.status_code == 202
    assert accepted.json() == {"runId": "RUN-NEW", "status": "PENDING"}


def test_SCN_DELIVERY_RETRY_001_页面仅在失败终态展示确认入口() -> None:
    source = (ROOT / "prototype" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert 'state.implementationRun?.status === "FAILED"' in source
    assert 'state.requirement.operationStatus === "IDLE"' in source
    assert "重新自动交付" in source
    assert "confirmed: true" in source
