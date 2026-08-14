from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import ROOT
from app.graph import load_object
from app.store import REQUIREMENT_ID, Store


def test_SCN_DELIVERY_RETRY_001_待补充信息运行可创建隔离重试(tmp_path: Path) -> None:
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
            ) VALUES ('RUN-NEEDS-INPUT', ?, ?, 'NEEDS_INPUT', '/tmp/旧-worktree',
                      '需要补充信息', '旧证据', ?, ?)
            """,
            (REQUIREMENT_ID, revision["id"], now, now),
        )

    run_id, _, _ = store.retry_delivery(str(revision["id"]), str(revision["contentHash"]))

    assert store.implementation_run("RUN-NEEDS-INPUT")["status"] == "NEEDS_INPUT"
    retried = store.implementation_run(run_id)
    assert retried["status"] == "PENDING"
    assert retried["worktree"] is None
    assert retried["validationContextHash"] is None


def test_SCN_DELIVERY_RETRY_001_页面对待补充信息终态展示重试入口() -> None:
    source = (ROOT / "prototype" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert 'state.implementationRun?.status === "NEEDS_INPUT"' in source
    assert "confirmed: true" in source
