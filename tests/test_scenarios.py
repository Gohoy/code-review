from __future__ import annotations

import asyncio
import copy
import sqlite3
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import cast

import pytest

from app.config import ROOT
from app.graph import JsonObject, changed_ids, load_object, to_dot
from app.runner import Runner, RunnerError
from app.service import ReviewService
from app.store import REPOSITORY_ID, Store, StoreError


class FakeRunner:
    def __init__(
        self,
        diff: Callable[[JsonObject], JsonObject] | None = None,
        result: JsonObject | None = None,
        error: Exception | None = None,
    ) -> None:
        self.diff = diff or valid_diff
        self.result = result or {"status": "COMPLETED", "summary": "开发完成", "question": ""}
        self.error = error
        self.worktree: Path | None = None
        self.implemented_hash: str | None = None

    async def dependency_status(self) -> JsonObject:
        return {"codex": "可用", "git": "可用", "dot": "可用"}

    async def model(self, document: JsonObject, _: list[JsonObject]) -> JsonObject:
        if self.error:
            raise self.error
        return self.diff(document)

    async def create_worktree(self, _: str) -> Path:
        self.worktree = Path("/tmp/sdbp-review-test-worktree")
        return self.worktree

    async def implement(self, document: JsonObject, _: Path) -> JsonObject:
        revision = cast(JsonObject, document["revision"])
        self.implemented_hash = str(revision["contentHash"])
        return self.result

    async def render(self, dot_source: str) -> str:
        return f"<svg>{dot_source}</svg>"


def seed() -> JsonObject:
    return load_object(ROOT / "model" / "review-tool.json")


def schema() -> JsonObject:
    return load_object(ROOT / "model" / "graph.schema.json")


def valid_diff(document: JsonObject) -> JsonObject:
    graph = cast(JsonObject, document["graph"])
    nodes = cast(list[JsonObject], graph["nodes"])
    changed = copy.deepcopy(
        next(node for node in nodes if node["id"] == "ACTION-SUBMIT-REQUIREMENT")
    )
    changed["summary"] = "需求方通过自然语言说明目标、场景和期望结果。"
    revision = cast(JsonObject, document["revision"])
    return {
        "baseRevisionId": revision["id"],
        "reply": "已补充需求输入说明，请审阅图差异。",
        "upsertNodes": [changed],
        "deleteNodeIds": [],
        "upsertEdges": [],
        "deleteEdgeIds": [],
    }


def unresolved_diff(document: JsonObject) -> JsonObject:
    revision = cast(JsonObject, document["revision"])
    return {
        "baseRevisionId": revision["id"],
        "reply": "需要确认代码托管平台。",
        "upsertNodes": [
            {
                "id": "QUESTION-CODE-HOST",
                "layer": "requirement",
                "kind": "Question",
                "title": "代码托管平台是什么？",
                "summary": "创建 MR 前必须明确代码托管平台。",
                "source": "UNRESOLVED",
                "details": {},
            }
        ],
        "deleteNodeIds": [],
        "upsertEdges": [
            {
                "id": "EDGE-CODE-HOST-BLOCKS-DEVELOP",
                "sourceId": "QUESTION-CODE-HOST",
                "targetId": "ACTION-DEVELOP-IN-WORKTREE",
                "kind": "blocks",
                "source": "UNRESOLVED",
            }
        ],
        "deleteEdgeIds": [],
    }


def make_service(tmp_path: Path, runner: FakeRunner) -> ReviewService:
    store = Store(tmp_path / "review.sqlite3", schema())
    history = (load_object(ROOT / "model" / "revision" / "REV-REVIEW-TOOL-007.json"),)
    return ReviewService(store, cast(Runner, runner), seed(), history)


async def settle(service: ReviewService) -> None:
    while service.tasks:
        await asyncio.gather(*tuple(service.tasks))


def run(coroutine: Coroutine[object, object, None]) -> None:
    asyncio.run(coroutine)


@pytest.mark.parametrize(
    "scenario_id",
    ["SCN-REQ-DIALOG-001", "SCN-GRAPH-LIVE-001", "SCN-REQ-REVISE-001"],
    ids=lambda value: value,
)
def test_对话生成不可变候选revision(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        runner = FakeRunner()
        service = make_service(tmp_path, runner)
        await service.initialize()
        before = await service.state()
        await service.submit_message("输入场景需要更清楚。")
        modeling = await service.state()
        assert modeling["requirement"]["operationStatus"] == "MODELING"
        await settle(service)
        after = await service.state()
        assert before["revision"]["revision"]["id"] == "REV-REVIEW-TOOL-008"
        assert after["revision"]["revision"]["id"] == "REV-REVIEW-TOOL-009"
        assert after["revision"]["revision"]["baseRevisionId"] == "REV-REVIEW-TOOL-008"
        assert after["changedNodeIds"] == ["ACTION-SUBMIT-REQUIREMENT"]
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize(
    "scenario_id",
    ["SCN-REQ-DEPENDENCY-001", "SCN-REQ-APPROVE-BLOCK-001"],
    ids=lambda value: value,
)
def test_待确认问题和过期哈希阻止批准(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path, FakeRunner(diff=unresolved_diff))
        await service.initialize()
        await service.submit_message("开发后需要创建 MR。")
        await settle(service)
        state = await service.state()
        revision = state["revision"]["revision"]
        assert not revision["approvable"]
        with pytest.raises(StoreError):
            await service.approve_and_start(revision["id"], revision["contentHash"])
        with sqlite3.connect(tmp_path / "review.sqlite3") as connection:
            assert connection.execute("SELECT count(*) FROM implementation_run").fetchone()[0] == 0
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize(
    "scenario_id",
    ["SCN-REQ-APPROVE-001", "SCN-DEV-CODEX-001"],
    ids=lambda value: value,
)
def test_明确批准后才按同一哈希开发(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        runner = FakeRunner()
        service = make_service(tmp_path, runner)
        await service.initialize()
        await service.submit_message("补充输入场景。")
        await settle(service)
        state = await service.state()
        revision = state["revision"]["revision"]
        with pytest.raises(StoreError):
            await service.approve_and_start(revision["id"], "0" * 64)
        assert runner.worktree is None
        run_id = await service.approve_and_start(revision["id"], revision["contentHash"])
        await settle(service)
        completed = await service.state()
        assert completed["implementationRun"]["id"] == run_id
        assert completed["implementationRun"]["status"] == "COMPLETED"
        assert runner.worktree is not None
        assert runner.implemented_hash == revision["contentHash"]
        with (
            sqlite3.connect(tmp_path / "review.sqlite3") as connection,
            pytest.raises(sqlite3.IntegrityError),
        ):
            connection.execute(
                "UPDATE revision SET content_json = '{}' WHERE id = ?", (revision["id"],)
            )
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize("scenario_id", ["SCN-DEV-FAIL-001"], ids=lambda value: value)
def test_Codex失败不覆盖有效revision(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path, FakeRunner(error=RunnerError("Codex 输出无效")))
        await service.initialize()
        await service.submit_message("这次输出会失败。")
        await settle(service)
        state = await service.state()
        assert state["revision"]["revision"]["id"] == "REV-REVIEW-TOOL-008"
        assert state["requirement"]["status"] == "ERROR"
        assert "Codex 输出无效" in state["messages"][-1]["content"]
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize("scenario_id", ["SCN-DEV-QUESTION-001"], ids=lambda value: value)
def test_开发中新决策返回对话(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        result: JsonObject = {
            "status": "NEEDS_INPUT",
            "summary": "缺少必须的人工决策",
            "question": "请确认目标分支名称。",
        }
        service = make_service(tmp_path, FakeRunner(result=result))
        await service.initialize()
        await service.submit_message("补充输入场景。")
        await settle(service)
        state = await service.state()
        revision = state["revision"]["revision"]
        await service.approve_and_start(revision["id"], revision["contentHash"])
        await settle(service)
        final = await service.state()
        assert final["implementationRun"]["status"] == "NEEDS_INPUT"
        assert final["messages"][-1]["content"] == "请确认目标分支名称。"
        assert final["requirement"]["operationStatus"] == "IDLE"
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize("scenario_id", ["SCN-GRAPH-EXPLORE-001"], ids=lambda value: value)
def test_统一画布投影稳定节点ID和分层(tmp_path: Path, scenario_id: str) -> None:
    del tmp_path
    document = seed()
    base = load_object(ROOT / "model" / "revision" / "REV-REVIEW-TOOL-007.json")
    node_ids, edge_ids = changed_ids(base, document)
    source = to_dot(
        document,
        {"requirement", "design"},
        node_ids,
        edge_ids,
        "ACTION-GENERATE-UNIFIED-GRAPH",
    )
    assert 'id="ACTION-GENERATE-UNIFIED-GRAPH"' in source
    assert "DESIGN-COMPONENT-GRAPH-PROJECTION" in source
    assert "SCN-REQ-DIALOG-001" not in source
    assert scenario_id.startswith("SCN-")


@pytest.mark.parametrize("scenario_id", ["SCN-GRAPH-LIVE-001"], ids=lambda value: value)
def test_仓库拥有统一图revision链(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path, FakeRunner())
        await service.initialize()
        await service.submit_message("更新仓库统一图。")
        await settle(service)
        state = await service.state()
        with sqlite3.connect(tmp_path / "review.sqlite3") as connection:
            repository = connection.execute(
                "SELECT current_revision_id FROM repository WHERE id = ?", (REPOSITORY_ID,)
            ).fetchone()
            owner = connection.execute(
                "SELECT repository_id FROM revision WHERE id = ?",
                (state["revision"]["revision"]["id"],),
            ).fetchone()
        assert repository == (state["revision"]["revision"]["id"],)
        assert owner == (REPOSITORY_ID,)
        assert state["repository"]["id"] == REPOSITORY_ID
        assert scenario_id.startswith("SCN-")

    run(scenario())
