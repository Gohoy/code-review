from __future__ import annotations

import copy
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from app.config import ROOT
from app.graph import JsonObject, load_object
from app.runner import Runner
from app.service import ReviewService
from app.store import REQUIREMENT_ID, Store


class 空运行器:
    async def dependency_status(self) -> JsonObject:
        return {}


def _存储(tmp_path: Path) -> tuple[Store, JsonObject]:
    document = copy.deepcopy(load_object(ROOT / "model" / "review-tool.json"))
    revision = cast(JsonObject, document["revision"])
    store = Store(tmp_path / "review.sqlite3", load_object(ROOT / "model" / "graph.schema.json"))
    store.initialize(document)
    return store, revision


def _运行(store: Store, revision_id: str, run_id: str, status: str, created_at: str) -> None:
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            """
            INSERT INTO implementation_run (
                id, requirement_id, revision_id, status, worktree, summary,
                validation_context_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '/tmp/test-worktree', '', NULL, ?, ?)
            """,
            (run_id, REQUIREMENT_ID, revision_id, status, created_at, created_at),
        )


def _证据() -> tuple[JsonObject, ...]:
    return (
        {
            "scenarioId": "SCN-RUN-TEST-EVIDENCE-001",
            "testNodeId": "tests/test_SCN-RUN-TEST-EVIDENCE-001.py::test_逐测试证据",
            "status": "PASS",
            "source": "OBSERVED",
        },
    )


def test_SCN_RUN_TEST_EVIDENCE_001_固定测试通过后原子保存结构化证据(
    tmp_path: Path,
) -> None:
    store, revision = _存储(tmp_path)
    revision_id = str(revision["id"])
    _运行(store, revision_id, "RUN-CURRENT", "TESTING", "2026-08-14T01:00:00+00:00")

    store.finish_test("RUN-CURRENT", True, "固定测试通过", "context-hash", _证据())

    result = store.latest_run_test_evidence(revision_id)
    assert result["run"]["id"] == "RUN-CURRENT"
    assert result["evidence"] == [
        {
            **_证据()[0],
            "implementationRunId": "RUN-CURRENT",
            "revisionId": revision_id,
            "observedAt": result["evidence"][0]["observedAt"],
        }
    ]


@pytest.mark.parametrize("latest_status", ["RUNNING", "FAILED", "BLOCKED", "COMPLETED"])
def test_SCN_RUN_TEST_EVIDENCE_001_最新运行不会借用旧运行证据(
    tmp_path: Path, latest_status: str
) -> None:
    store, revision = _存储(tmp_path)
    revision_id = str(revision["id"])
    _运行(store, revision_id, "RUN-OLD", "TESTING", "2026-08-14T01:00:00+00:00")
    store.finish_test("RUN-OLD", True, "固定测试通过", "old-hash", _证据())
    _运行(store, revision_id, "RUN-LATEST", latest_status, "2026-08-14T02:00:00+00:00")

    result = store.latest_run_test_evidence(revision_id)

    assert result["run"]["id"] == "RUN-LATEST"
    assert result["run"]["status"] == latest_status
    assert result["evidence"] == []


def test_SCN_RUN_TEST_EVIDENCE_001_投影包含场景关系逐测试节点和空状态(
    tmp_path: Path,
) -> None:
    store, revision = _存储(tmp_path)
    service = ReviewService(store, cast(Runner, 空运行器()), {})
    revision_id = str(revision["id"])
    run = {"id": "RUN-CURRENT", "status": "VERIFIED"}
    item = {
        **_证据()[0],
        "implementationRunId": "RUN-CURRENT",
        "revisionId": revision_id,
        "observedAt": "2026-08-14T01:00:00+00:00",
    }
    populated: JsonObject = {"nodes": [], "edges": []}
    service._project_run_test_evidence(populated, {"run": run, "evidence": [item]}, revision_id)
    nodes = cast(list[JsonObject], populated["nodes"])
    edges = cast(list[JsonObject], populated["edges"])

    assert [node["title"] for node in nodes] == [
        "SCN-RUN-TEST-EVIDENCE-001 测试汇总",
        item["testNodeId"],
    ]
    assert {(edge["kind"], edge["sourceId"], edge["targetId"]) for edge in edges} == {
        (
            "verified_by",
            "SCN-RUN-TEST-EVIDENCE-001",
            "EVIDENCE-RUN-SUMMARY-RUN-CURRENT-SCN-RUN-TEST-EVIDENCE-001",
        ),
        (
            "contains",
            "EVIDENCE-RUN-SUMMARY-RUN-CURRENT-SCN-RUN-TEST-EVIDENCE-001",
            "EVIDENCE-RUN-TEST-RUN-CURRENT-SCN-RUN-TEST-EVIDENCE-001-1",
        ),
    }

    empty: JsonObject = {"nodes": [], "edges": []}
    service._project_run_test_evidence(
        empty, {"run": {"id": "RUN-BLOCKED", "status": "BLOCKED"}, "evidence": []}, revision_id
    )
    assert empty["nodes"][0]["title"] == "当前运行暂无测试证据"
    assert empty["nodes"][0]["details"]["status"] == "BLOCKED"


def test_SCN_RUN_TEST_EVIDENCE_001_同一运行重新测试时先清除旧证据(tmp_path: Path) -> None:
    store, revision = _存储(tmp_path)
    revision_id = str(revision["id"])
    _运行(store, revision_id, "RUN-RETRY", "TESTING", "2026-08-14T01:00:00+00:00")
    store.finish_test("RUN-RETRY", True, "固定测试通过", "context-hash", _证据())
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE implementation_run SET status = 'VERIFIED' WHERE id = 'RUN-RETRY'"
        )

    store.begin_test("RUN-RETRY")

    assert store.latest_run_test_evidence(revision_id)["evidence"] == []


def test_SCN_RUN_TEST_EVIDENCE_001_前端在桌面侧栏与移动抽屉共用可点击证据详情() -> None:
    helper = (ROOT / "prototype" / "src" / "graph.js").read_text(encoding="utf-8")
    source = (ROOT / "prototype" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert 'layer !== "verification"' in helper
    assert "...(projection.nodes || [])" in helper
    assert "...(projection.edges || [])" in helper
    assert "mergeTestEvidenceGraph(document?.graph, layer, testEvidence)" in source
    assert "value.implementationRunId === implementationRunId" in source
    assert "desktop && <Sider" in source
    assert 'title="节点详情"' in source
    assert "if (!desktop) setInspectorOpen(true)" in source
    assert "观察时间" in source
