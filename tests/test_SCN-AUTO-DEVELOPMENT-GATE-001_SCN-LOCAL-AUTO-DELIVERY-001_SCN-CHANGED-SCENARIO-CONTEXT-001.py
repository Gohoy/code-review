from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from app.service import revision_change_context, validation_context_hash
from app.store import Store, StoreError


def _store(tmp_path: Path) -> Store:
    path = tmp_path / "review.sqlite3"
    base = {
        "revision": {"id": "REV-BASE", "baseRevisionId": None},
        "graph": {"nodes": [], "edges": []},
    }
    current = {
        "revision": {"id": "REV-CURRENT", "baseRevisionId": "REV-BASE"},
        "graph": {
            "nodes": [
                {"id": "SCN-LOCAL-AUTO-DELIVERY-001", "kind": "Scenario"},
                {"id": "SCN-AUTO-DEVELOPMENT-GATE-001", "kind": "Scenario"},
                {"id": "SCN-CHANGED-SCENARIO-CONTEXT-001", "kind": "Scenario"},
            ],
            "edges": [],
        },
    }
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE revision (
                id TEXT PRIMARY KEY, base_revision_id TEXT, content_json TEXT NOT NULL
            );
            CREATE TABLE implementation_run (
                id TEXT PRIMARY KEY, revision_id TEXT NOT NULL, status TEXT NOT NULL,
                worktree TEXT, summary TEXT, review_status TEXT, review_summary TEXT,
                validation_context_hash TEXT, created_at TEXT, updated_at TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO revision VALUES (?, ?, ?)",
            [
                ("REV-BASE", None, json.dumps(base)),
                ("REV-CURRENT", "REV-BASE", json.dumps(current)),
            ],
        )
        connection.execute(
            """
            INSERT INTO implementation_run VALUES (
                'RUN-TEST', 'REV-CURRENT', 'TESTING', '/tmp/worktree', NULL,
                NULL, NULL, NULL, '1', '1'
            )
            """
        )
    return Store(path, {})


def _expected_hash(store: Store) -> str:
    base, current = store.run_revision_documents("RUN-TEST")
    changed = cast(list[str], revision_change_context(base, current)["changedScenarioIds"])
    assert changed == [
        "SCN-AUTO-DEVELOPMENT-GATE-001",
        "SCN-CHANGED-SCENARIO-CONTEXT-001",
        "SCN-LOCAL-AUTO-DELIVERY-001",
    ]
    return validation_context_hash(changed)


def test_SCN_AUTO_DEVELOPMENT_GATE_001_SCN_LOCAL_AUTO_DELIVERY_001_SCN_CHANGED_SCENARIO_CONTEXT_001_固定测试持久化上下文(  # noqa: E501
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    context_hash = _expected_hash(store)
    store.finish_test("RUN-TEST", True, "固定测试通过", context_hash)
    run = store.implementation_run("RUN-TEST")
    assert run["status"] == "VERIFIED"
    assert run["validationContextHash"] == context_hash


def test_SCN_AUTO_DEVELOPMENT_GATE_001_SCN_LOCAL_AUTO_DELIVERY_001_SCN_CHANGED_SCENARIO_CONTEXT_001_原子门禁拒绝不一致上下文(  # noqa: E501
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    context_hash = _expected_hash(store)
    store.finish_test("RUN-TEST", True, "固定测试通过", context_hash)
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            UPDATE implementation_run
            SET status = 'REVIEW_PASSED', review_status = 'PASS'
            WHERE id = 'RUN-TEST'
            """
        )

    with pytest.raises(TypeError):
        store.begin_merge_verified("RUN-TEST")  # type: ignore[call-arg]
    for invalid_hash in ("", "wrong"):
        with pytest.raises(StoreError):
            store.begin_merge_verified("RUN-TEST", invalid_hash)
        assert store.implementation_run("RUN-TEST")["status"] == "REVIEW_PASSED"

    worktree, revision_id = store.begin_merge_verified("RUN-TEST", context_hash)
    assert (worktree, revision_id) == (Path("/tmp/worktree"), "REV-CURRENT")
    assert store.implementation_run("RUN-TEST")["status"] == "MERGING"
