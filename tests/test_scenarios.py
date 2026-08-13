from __future__ import annotations

import asyncio
import copy
import hashlib
import sqlite3
import subprocess
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import cast

import pytest

from app.config import ROOT, Settings
from app.graph import (
    GraphError,
    JsonObject,
    apply_diff,
    changed_ids,
    code_index_diff,
    graph_hash,
    load_object,
    requirement_context,
    to_dot,
    validate_document,
    with_code_snapshot,
)
from app.indexer import index_repository
from app.prompt import Prompt
from app.runner import Runner, RunnerError
from app.service import ReviewService
from app.store import REPOSITORY_ID, Store, StoreError


class FakeRunner:
    def __init__(
        self,
        diff: Callable[[JsonObject], JsonObject] | None = None,
        result: JsonObject | None = None,
        error: Exception | None = None,
        verification_error: Exception | None = None,
        skip_test: bool = False,
        review_status: str = "PASS",
    ) -> None:
        self.diff = diff or valid_diff
        self.result = result
        self.error = error
        self.verification_error = verification_error
        self.skip_test = skip_test
        self.review_status = review_status
        self.store: Store | None = None
        self.worktree: Path | None = None
        self.implemented_hash: str | None = None
        self.verified = False
        self.merged = False

    async def dependency_status(self) -> JsonObject:
        return {"codex": "可用", "git": "可用", "dot": "可用"}

    def prompt(self, task: str, context: JsonObject) -> Prompt:
        text = f"{task}:{context}"
        return Prompt(task.lower(), "test", text, hashlib.sha256(text.encode()).hexdigest())

    async def agent(
        self,
        task: str,
        _: Prompt,
        agent_run_id: str,
        implementation_run_id: str | None = None,
        worktree: Path | None = None,
    ) -> JsonObject:
        del worktree
        if self.error and task in {"REQUIREMENT_CHANGE", "REPOSITORY_BASELINE"}:
            raise self.error
        store = self._store()
        if task == "REPOSITORY_BASELINE":
            document = store.current_document()
            revision = cast(JsonObject, document["revision"])
            function: JsonObject = {
                "id": "IMPL-SYMBOL-BASELINE-TEST",
                "name": "baseline",
                "qualifiedName": "baseline",
                "kind": "function_definition",
                "path": "app/example.py",
                "range": {
                    "start": {"line": 1, "column": 1},
                    "end": {"line": 2, "column": 1},
                },
                "fingerprint": "a" * 64,
                "snapshotId": "SNAPSHOT-REPOSITORY-LOCAL-BASELINE01",
                "extractorId": "PYTHON-AST",
                "graphNodeIds": [],
                "mappedNodeIds": [],
                "calls": [],
                "callSites": [],
                "coverage": {
                    "status": "UNKNOWN",
                    "artifact": None,
                    "coveredLineCount": None,
                },
            }
            index: JsonObject = {
                "snapshot": {
                    "id": "SNAPSHOT-REPOSITORY-LOCAL-BASELINE01",
                    "repositoryId": "REPOSITORY-LOCAL",
                    "commitSha": "1" * 40,
                    "treeHash": "2" * 40,
                    "scanHash": "3" * 64,
                    "roots": ["app"],
                    "extractors": [{"id": "PYTHON-AST", "version": "3.13"}],
                },
                "coverage": {
                    "functionCount": 1,
                    "graphFunctionCount": 0,
                    "mappedFunctionCount": 0,
                    "unmappedFunctionCount": 1,
                    "status": "UNKNOWN",
                    "measuredFunctionCount": 0,
                    "coveredFunctionCount": 0,
                    "uncoveredFunctionCount": 0,
                    "artifact": None,
                },
                "fileFacts": [
                    {
                        "path": "app/example.py",
                        "extractorId": "PYTHON-AST",
                        "fingerprint": "b" * 64,
                        "range": {
                            "start": {"line": 1, "column": 0},
                            "end": {"line": 2, "column": 1},
                        },
                    }
                ],
                "functions": [function],
                "errors": [],
            }
            store.sync_code_index(agent_run_id, index)
            return {
                "status": "AWAITING_APPROVAL",
                "reply": "代码基线候选已生成。",
                "focusNodeIds": ["SCN-REPOSITORY-BASELINE-001"],
            }
        if task == "REQUIREMENT_CHANGE":
            document = store.current_document()
            diff = self.diff(document)
            tool_id = store.begin_tool(
                agent_run_id, None, "graph_create_candidate", str(diff["baseRevisionId"])
            )
            candidate = store.create_candidate(agent_run_id, diff)
            store.finish_tool(tool_id, "COMPLETED", "候选 revision 已创建")
            revision = cast(JsonObject, candidate["revision"])
            status = "AWAITING_APPROVAL" if revision["approvable"] else "NEEDS_INPUT"
            return {
                "status": status,
                "reply": str(diff["reply"]),
                "focusNodeIds": ["ACTION-SUBMIT-REQUIREMENT"],
            }
        if implementation_run_id is None:
            raise AssertionError("开发 Agent 缺少运行 ID")
        if task == "IMPLEMENTATION":
            if self.result is not None:
                return self.result
            current = store.current_document()
            revision = cast(JsonObject, current["revision"])
            self.implemented_hash = str(revision["contentHash"])
            self.worktree = Path("/tmp/sdbp-review-test-worktree")
            self._tool(
                agent_run_id,
                implementation_run_id,
                "development_start",
                lambda: store.start_run(implementation_run_id, self.worktree),
            )
            self._tool(
                agent_run_id,
                implementation_run_id,
                "change_submit",
                lambda: store.submit_change(implementation_run_id, "实现已完成"),
            )
            if self.skip_test:
                return {"status": "COMPLETED", "reply": "实现完成", "focusNodeIds": []}
            self.verified = True
            store.begin_test(implementation_run_id)
            test_tool = store.begin_tool(
                agent_run_id, implementation_run_id, "test_run", "执行固定测试"
            )
            if self.verification_error:
                store.finish_test(implementation_run_id, False, str(self.verification_error))
                store.finish_tool(test_tool, "FAILED", str(self.verification_error))
                raise self.verification_error
            store.finish_test(implementation_run_id, True, "固定验证全部通过")
            store.finish_tool(test_tool, "COMPLETED", "固定验证全部通过")
            return {"status": "COMPLETED", "reply": "实现和固定测试已完成", "focusNodeIds": []}
        if task != "SEMANTIC_REVIEW":
            raise AssertionError(f"未知测试 Agent 任务：{task}")
        status = self.review_status
        self._tool(
            agent_run_id,
            implementation_run_id,
            "review_submit",
            lambda: store.submit_review(implementation_run_id, status, "语义 Review 完成"),
        )
        if status == "BLOCKED":
            return {
                "status": "BLOCKED",
                "reply": "语义 Review 发现阻断问题",
                "focusNodeIds": ["SCN-AGENT-SEMANTIC-REVIEW-001"],
            }
        merge_tool = store.begin_tool(
            agent_run_id, implementation_run_id, "delivery_merge", "安全合并"
        )
        store.begin_merge(implementation_run_id)
        self.merged = True
        store.complete_delivery(implementation_run_id, "已自动合并到本地分支")
        store.finish_tool(merge_tool, "COMPLETED", "已自动合并到本地分支")
        return {"status": "COMPLETED", "reply": "Review 和合并已完成", "focusNodeIds": []}

    def _tool(
        self,
        agent_run_id: str,
        implementation_run_id: str,
        name: str,
        operation: Callable[[], None],
    ) -> None:
        store = self._store()
        tool_id = store.begin_tool(agent_run_id, implementation_run_id, name, name)
        operation()
        store.finish_tool(tool_id, "COMPLETED", f"{name} 完成")

    def _store(self) -> Store:
        if self.store is None:
            raise AssertionError("测试 Runner 尚未绑定 Store")
        return self.store

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
    document = seed()
    revision = cast(JsonObject, document["revision"])
    store = Store(tmp_path / "review.sqlite3", schema())
    runner.store = store
    history = (load_object(ROOT / "model" / "revision" / f"{revision['baseRevisionId']}.json"),)
    return ReviewService(store, cast(Runner, runner), document, history)


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
        assert modeling["requirement"]["operationStatus"] == "AGENT_RUNNING"
        await settle(service)
        after = await service.state()
        before_id = before["revision"]["revision"]["id"]
        assert after["revision"]["revision"]["id"] != before_id
        assert after["revision"]["revision"]["baseRevisionId"] == before_id
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
        assert "已自动合并" in completed["implementationRun"]["summary"]
        assert runner.worktree is not None
        assert runner.implemented_hash == revision["contentHash"]
        assert runner.verified
        assert runner.merged
        with (
            sqlite3.connect(tmp_path / "review.sqlite3") as connection,
            pytest.raises(sqlite3.IntegrityError),
        ):
            connection.execute(
                "UPDATE revision SET content_json = '{}' WHERE id = ?", (revision["id"],)
            )
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize("scenario_id", ["SCN-LOCAL-AUTO-DELIVERY-001"], ids=lambda value: value)
def test_本地验证通过后才自动合并(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        runner = FakeRunner(verification_error=RunnerError("固定验证失败"))
        service = make_service(tmp_path, runner)
        await service.initialize()
        await service.submit_message("补充本地自动交付场景。")
        await settle(service)
        state = await service.state()
        revision = state["revision"]["revision"]
        await service.approve_and_start(revision["id"], revision["contentHash"])
        await settle(service)
        failed = await service.state()
        assert failed["implementationRun"]["status"] == "FAILED"
        assert "固定验证失败" in failed["implementationRun"]["summary"]
        assert runner.verified
        assert not runner.merged
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize(
    "scenario_id", ["SCN-AGENT-MCP-ORCHESTRATION-001"], ids=lambda value: value
)
def test_Agent未调用固定测试不能进入Review(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        runner = FakeRunner(skip_test=True)
        service = make_service(tmp_path, runner)
        await service.initialize()
        await service.submit_message("补充 Agent 门禁场景。")
        await settle(service)
        revision = (await service.state())["revision"]["revision"]
        await service.approve_and_start(revision["id"], revision["contentHash"])
        await settle(service)
        state = await service.state()
        assert state["implementationRun"]["status"] == "FAILED"
        assert "未通过 MCP 完成固定测试" in state["implementationRun"]["summary"]
        assert not runner.merged
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize("scenario_id", ["SCN-AGENT-SEMANTIC-REVIEW-001"], ids=lambda value: value)
def test_语义Review阻断时不能合并(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        runner = FakeRunner(review_status="BLOCKED")
        service = make_service(tmp_path, runner)
        await service.initialize()
        await service.submit_message("补充语义 Review 场景。")
        await settle(service)
        revision = (await service.state())["revision"]["revision"]
        await service.approve_and_start(revision["id"], revision["contentHash"])
        await settle(service)
        state = await service.state()
        assert state["implementationRun"]["status"] == "BLOCKED"
        assert state["implementationRun"]["reviewStatus"] == "BLOCKED"
        assert not runner.merged
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize("scenario_id", ["SCN-LOCAL-AUTO-DELIVERY-001"], ids=lambda value: value)
def test_本地自动合并只接受干净原基线(tmp_path: Path, scenario_id: str) -> None:
    repository = tmp_path / "repository"

    def git(*arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    repository.mkdir()
    git("init", "--initial-branch=main")
    git("config", "user.name", "测试用户")
    git("config", "user.email", "test@example.invalid")
    (repository / "功能.txt").write_text("旧实现\n", encoding="utf-8")
    git("add", "功能.txt")
    git("commit", "-m", "chore: 初始化测试仓库")
    worktree = tmp_path / "worktree"
    git("worktree", "add", "--detach", str(worktree), "HEAD")
    (worktree / "功能.txt").write_text("新实现\n", encoding="utf-8")
    settings = Settings(
        host="127.0.0.1",
        port=0,
        data_dir=tmp_path / "data",
        repository=repository,
        worktree_root=tmp_path / "managed-worktree",
        web_dir=ROOT / "prototype" / "dist" / "client",
        model_path=ROOT / "model" / "review-tool.json",
        revision_dir=ROOT / "model" / "revision",
        graph_schema_path=ROOT / "model" / "graph.schema.json",
        agent_result_schema_path=ROOT / "app" / "schema" / "agent-result.schema.json",
        prompt_dir=ROOT / "prompt",
    )
    runner = Runner(settings)

    async def scenario() -> None:
        result = await runner.merge(worktree, "REV-TEST-001")
        assert "main" in result
        assert (repository / "功能.txt").read_text(encoding="utf-8") == "新实现\n"

        dirty_worktree = tmp_path / "dirty-worktree"
        git("worktree", "add", "--detach", str(dirty_worktree), "HEAD")
        (dirty_worktree / "功能.txt").write_text("下一版\n", encoding="utf-8")
        (repository / "功能.txt").write_text("本地未提交变化\n", encoding="utf-8")
        with pytest.raises(RunnerError, match="未提交变化"):
            await runner.merge(dirty_worktree, "REV-TEST-002")
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize("scenario_id", ["SCN-DEV-FAIL-001"], ids=lambda value: value)
def test_Codex失败不覆盖有效revision(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path, FakeRunner(error=RunnerError("Codex 输出无效")))
        await service.initialize()
        before = await service.state()
        await service.submit_message("这次输出会失败。")
        await settle(service)
        state = await service.state()
        assert state["revision"]["revision"]["id"] == before["revision"]["revision"]["id"]
        assert state["requirement"]["status"] == "FAILED"
        assert "Codex 输出无效" in state["messages"][-1]["content"]
        assert scenario_id.startswith("SCN-")

    run(scenario())


@pytest.mark.parametrize("scenario_id", ["SCN-DEV-QUESTION-001"], ids=lambda value: value)
def test_开发中新决策返回对话(tmp_path: Path, scenario_id: str) -> None:
    async def scenario() -> None:
        result: JsonObject = {
            "status": "NEEDS_INPUT",
            "reply": "请确认目标分支名称。",
            "focusNodeIds": [],
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
    scenario = next(
        node
        for node in cast(list[JsonObject], cast(JsonObject, document["graph"])["nodes"])
        if node["id"] == scenario_id
    )
    outcomes = cast(JsonObject, scenario["details"])["then"]
    assert any("需求对话默认关闭" in item for item in outcomes)
    assert any("鼠标滚轮" in item for item in outcomes)
    revision = cast(JsonObject, document["revision"])
    base = load_object(ROOT / "model" / "revision" / f"{revision['baseRevisionId']}.json")
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
    implementation = to_dot(document, {"implementation"}, node_ids, edge_ids, None)
    assert 'splines="polyline"' in implementation
    assert scenario_id.startswith("SCN-")


@pytest.mark.parametrize(
    "scenario_id", ["SCN-REQUIREMENT-CODE-DRILLDOWN-001"], ids=lambda value: value
)
def test_需求图展示经技术设计映射到代码的需求(tmp_path: Path, scenario_id: str) -> None:
    del tmp_path
    document = seed()
    graph = cast(JsonObject, document["graph"])
    cast(list[JsonObject], graph["edges"]).append(
        {
            "id": "EDGE-SCANNER-IMPLEMENTED-BY-VERIFY",
            "sourceId": "DESIGN-COMPONENT-CODE-SCANNER",
            "targetId": "IMPL-SYMBOL-RUNNER-VERIFY",
            "kind": "implemented_by",
            "source": "INFERRED",
        }
    )
    source = to_dot(document, {"requirement"}, set(), set(), None)
    assert 'id="SCN-REPOSITORY-BASELINE-001"' in source
    context = requirement_context(document, "SCN-REPOSITORY-BASELINE-001")
    assert context["codePath"][0]["id"] == "IMPL-SYMBOL-RUNNER-VERIFY"
    assert scenario_id.startswith("SCN-")


@pytest.mark.parametrize(
    "scenario_id", ["SCN-REQUIREMENT-CODE-DRILLDOWN-001"], ids=lambda value: value
)
def test_需求节点投影相关主流程和真实代码链(tmp_path: Path, scenario_id: str) -> None:
    del tmp_path
    document = seed()
    graph = cast(JsonObject, document["graph"])
    context = requirement_context(document, "SCN-LOCAL-AUTO-DELIVERY-001")
    assert "ACTION-SUBMIT-REQUIREMENT" in context["pathNodeIds"]
    assert "ACTION-MERGE-LOCAL" in context["pathNodeIds"]
    assert [item["id"] for item in context["codePath"]] == [
        "IMPL-ENTRY-HTTP-APPROVE",
        "IMPL-SYMBOL-REVIEW-SERVICE-APPROVE",
        "IMPL-SYMBOL-STORE-APPROVE",
        "IMPL-SYMBOL-REVIEW-SERVICE-IMPLEMENT",
        "IMPL-SYMBOL-RUNNER-VERIFY",
        "IMPL-SYMBOL-RUNNER-MERGE",
        "IMPL-SYMBOL-STORE-FINISH-RUN",
    ]

    nodes = cast(list[JsonObject], graph["nodes"])
    edges = cast(list[JsonObject], graph["edges"])
    nodes[:] = [node for node in nodes if node["layer"] != "implementation"]
    edges[:] = [
        edge
        for edge in edges
        if not edge["sourceId"].startswith("IMPL-") and not edge["targetId"].startswith("IMPL-")
    ]
    snapshot_id = "SNAPSHOT-REPOSITORY-LOCAL-A1B2C3D4"
    anchor: JsonObject = {
        "snapshotId": snapshot_id,
        "path": "app/http.py",
        "range": {
            "start": {"line": 52, "column": 5},
            "end": {"line": 59, "column": 1},
        },
        "extractorId": "PYTHON-AST",
        "fingerprint": "a" * 64,
    }
    graph["codeSnapshots"] = [
        {
            "id": snapshot_id,
            "repositoryId": "REPOSITORY-LOCAL",
            "commitSha": "1" * 40,
            "treeHash": "2" * 40,
            "scanHash": "3" * 64,
            "roots": ["app"],
            "extractors": [{"id": "PYTHON-AST", "version": "1.0.0"}],
        }
    ]
    nodes.extend(
        [
            {
                "id": "IMPL-ENTRY-APPROVE",
                "layer": "implementation",
                "kind": "EntryPoint",
                "title": "批准需求接口",
                "summary": "接收批准请求。",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "anchors": [anchor],
                "details": {"qualifiedName": "POST /api/revision/{id}/approve-and-start"},
            },
            {
                "id": "IMPL-SYMBOL-APPROVE",
                "layer": "implementation",
                "kind": "Symbol",
                "title": "批准并开始开发",
                "summary": "冻结候选并启动开发。",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "anchors": [anchor],
                "details": {"qualifiedName": "ReviewService.approve_and_start"},
            },
        ]
    )
    edges.extend(
        [
            {
                "id": "EDGE-RULE-IMPLEMENTED-APPROVE",
                "sourceId": "DESIGN-RULE-LOCAL-AUTO-DELIVERY",
                "targetId": "IMPL-ENTRY-APPROVE",
                "kind": "implemented_by",
                "source": "DERIVED",
            },
            {
                "id": "EDGE-ENTRY-CALLS-APPROVE",
                "sourceId": "IMPL-ENTRY-APPROVE",
                "targetId": "IMPL-SYMBOL-APPROVE",
                "kind": "calls",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "anchors": [anchor],
            },
        ]
    )
    edges.append(
        {
            "id": "EDGE-MERGE-IMPLEMENTED-APPROVE",
            "sourceId": "ACTION-MERGE-LOCAL",
            "targetId": "IMPL-ENTRY-APPROVE",
            "kind": "implemented_by",
            "source": "DERIVED",
        }
    )
    context = requirement_context(document, "ACTION-MERGE-LOCAL")
    assert [item["id"] for item in context["codePath"]] == [
        "IMPL-ENTRY-APPROVE",
        "IMPL-SYMBOL-APPROVE",
    ]
    assert context["codePath"][0]["location"] == {"path": "app/http.py", "line": 52}
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


@pytest.mark.parametrize("scenario_id", ["SCN-GRAPH-LIVE-001"], ids=lambda value: value)
def test_启动时沿同一revision链加载内置候选(tmp_path: Path, scenario_id: str) -> None:
    store = Store(tmp_path / "review.sqlite3", schema())
    approved = load_object(ROOT / "model" / "revision" / "REV-REVIEW-TOOL-008.json")
    base = load_object(ROOT / "model" / "revision" / "REV-REVIEW-TOOL-009.json")
    candidate = load_object(ROOT / "model" / "revision" / "REV-REVIEW-TOOL-010.json")
    stale = copy.deepcopy(seed())
    stale_graph = cast(JsonObject, stale["graph"])
    stale_nodes = cast(list[JsonObject], stale_graph["nodes"])
    stale_nodes[0]["summary"] = "过期候选内容"
    cast(JsonObject, stale["revision"])["contentHash"] = graph_hash(stale_graph)
    store.initialize(approved)
    store.initialize(stale, (base, candidate))
    previous = load_object(ROOT / "model" / "revision" / "REV-REVIEW-TOOL-011.json")
    current_base = load_object(ROOT / "model" / "revision" / "REV-REVIEW-TOOL-012.json")
    store.initialize(seed(), (base, candidate, previous, current_base))
    current = store.state()["revision"]["revision"]
    assert current["id"] == "REV-REVIEW-TOOL-013"
    assert current["contentHash"] == seed()["revision"]["contentHash"]
    assert scenario_id.startswith("SCN-")


def test_SCN_REPOSITORY_BASELINE_001_页面任务写入全部函数候选(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path, FakeRunner())
        await service.initialize()
        agent_run_id = await service.start_repository_baseline()
        running = await service.state()
        assert running["agentRun"]["id"] == agent_run_id
        assert running["agentRun"]["task"] == "REPOSITORY_BASELINE"
        await settle(service)
        state = await service.state()
        implementation = [
            node
            for node in state["revision"]["graph"]["nodes"]
            if node["layer"] == "implementation"
        ]
        assert any(node["id"] == "IMPL-SYMBOL-BASELINE-TEST" for node in implementation)
        assert state["revision"]["revision"]["status"] == "CANDIDATE"
        assert state["agentRun"]["status"] == "AWAITING_APPROVAL"

    run(scenario())


def test_SCN_REPOSITORY_BASELINE_001_确定性同步函数调用和覆盖证据(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        "def caller():\n    callee()\n\ndef callee():\n    return 1\n", encoding="utf-8"
    )
    (tmp_path / "coverage.json").write_text(
        '{"files":{"service.py":{"executed_lines":[1,2]}}}', encoding="utf-8"
    )
    current = seed()
    graph = cast(JsonObject, current["graph"])
    index = index_repository(
        tmp_path,
        ["service.py"],
        "1" * 40,
        "2" * 40,
        graph,
    )
    base = with_code_snapshot(current, cast(JsonObject, index["snapshot"]))
    diff = code_index_diff(base, index)
    nodes = cast(list[JsonObject], diff["upsertNodes"])
    edges = cast(list[JsonObject], diff["upsertEdges"])

    assert sum(node["kind"] == "Symbol" for node in nodes) == 2
    assert sum(edge["kind"] == "calls" for edge in edges) == 1
    assert sum(node["kind"] == "Evidence" for node in nodes) == 1
    assert sum(edge["kind"] == "verified_by" for edge in edges) == 2


def test_SCN_REPOSITORY_BASELINE_001_刷新保留稳定函数语义映射(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    current = seed()
    graph = cast(JsonObject, current["graph"])
    first = index_repository(tmp_path, ["service.py"], "1" * 40, "2" * 40, graph)
    function_id = first["functions"][0]["id"]
    mapping_id = "EDGE-DESIGN-RUN-IMPLEMENTED"
    cast(list[JsonObject], graph["edges"]).append(
        {
            "id": mapping_id,
            "sourceId": "DESIGN-COMPONENT-CODE-SCANNER",
            "targetId": function_id,
            "kind": "implemented_by",
            "source": "INFERRED",
        }
    )

    refreshed = index_repository(tmp_path, ["service.py"], "3" * 40, "4" * 40, graph)
    base = with_code_snapshot(current, cast(JsonObject, refreshed["snapshot"]))
    diff = code_index_diff(base, refreshed)
    result = apply_diff(base, diff, "REV-REVIEW-TOOL-999", schema())

    assert mapping_id in {edge["id"] for edge in result["graph"]["edges"]}


@pytest.mark.parametrize("scenario_id", ["SCN-CODE-AUTHORITY-001"], ids=lambda value: value)
def test_图代码契约拒绝无快照或锚点的实现事实(tmp_path: Path, scenario_id: str) -> None:
    del tmp_path
    document = copy.deepcopy(seed())
    graph = cast(JsonObject, document["graph"])
    nodes = cast(list[JsonObject], graph["nodes"])
    edges = cast(list[JsonObject], graph["edges"])
    nodes[:] = [node for node in nodes if node["layer"] != "implementation"]
    edges[:] = [
        edge
        for edge in edges
        if not edge["sourceId"].startswith("IMPL-") and not edge["targetId"].startswith("IMPL-")
    ]
    snapshot_id = "SNAPSHOT-REPOSITORY-LOCAL-A1B2C3D4"
    anchor: JsonObject = {
        "snapshotId": snapshot_id,
        "path": "app/service.py",
        "range": {
            "start": {"line": 10, "column": 1},
            "end": {"line": 12, "column": 20},
        },
        "extractorId": "PYTHON-AST",
        "fingerprint": "a" * 64,
    }
    graph["codeSnapshots"] = [
        {
            "id": snapshot_id,
            "repositoryId": "REPOSITORY-LOCAL",
            "commitSha": "1" * 40,
            "treeHash": "2" * 40,
            "scanHash": "3" * 64,
            "roots": ["app"],
            "extractors": [{"id": "PYTHON-AST", "version": "1.0.0"}],
        }
    ]
    nodes.extend(
        [
            {
                "id": "IMPL-REPOSITORY-LOCAL",
                "layer": "implementation",
                "kind": "Repository",
                "title": "本地仓库",
                "summary": "固定代码快照所属仓库。",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "details": {},
            },
            {
                "id": "IMPL-SYMBOL-APPROVE",
                "layer": "implementation",
                "kind": "Symbol",
                "title": "批准实现",
                "summary": "处理批准操作。",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "anchors": [anchor],
                "details": {"qualifiedName": "ReviewService.approve"},
            },
            {
                "id": "IMPL-SYMBOL-STORE-APPROVE",
                "layer": "implementation",
                "kind": "Symbol",
                "title": "保存批准结果",
                "summary": "保存批准 revision。",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "anchors": [anchor],
                "details": {"qualifiedName": "Store.approve"},
            },
        ]
    )
    edges.extend(
        [
            {
                "id": "EDGE-IMPL-REPOSITORY-CONTAINS-APPROVE",
                "sourceId": "IMPL-REPOSITORY-LOCAL",
                "targetId": "IMPL-SYMBOL-APPROVE",
                "kind": "contains",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
            },
            {
                "id": "EDGE-IMPL-APPROVE-CALLS-STORE",
                "sourceId": "IMPL-SYMBOL-APPROVE",
                "targetId": "IMPL-SYMBOL-STORE-APPROVE",
                "kind": "calls",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "anchors": [anchor],
            },
            {
                "id": "EDGE-DESIGN-VALIDATOR-IMPLEMENTED-BY-APPROVE",
                "sourceId": "DESIGN-COMPONENT-GRAPH-VALIDATOR",
                "targetId": "IMPL-SYMBOL-APPROVE",
                "kind": "implemented_by",
                "source": "INFERRED",
            },
        ]
    )

    def refresh_hash(value: JsonObject) -> None:
        revision = cast(JsonObject, value["revision"])
        revision["contentHash"] = graph_hash(cast(JsonObject, value["graph"]))

    refresh_hash(document)
    validate_document(document, schema())

    without_node_anchor = copy.deepcopy(document)
    bad_nodes = cast(list[JsonObject], cast(JsonObject, without_node_anchor["graph"])["nodes"])
    next(node for node in bad_nodes if node["id"] == "IMPL-SYMBOL-APPROVE").pop("anchors")
    refresh_hash(without_node_anchor)
    with pytest.raises(GraphError, match="静态实现节点缺少源码锚点"):
        validate_document(without_node_anchor, schema())

    without_edge_anchor = copy.deepcopy(document)
    bad_edges = cast(list[JsonObject], cast(JsonObject, without_edge_anchor["graph"])["edges"])
    next(edge for edge in bad_edges if edge["id"] == "EDGE-IMPL-APPROVE-CALLS-STORE").pop("anchors")
    refresh_hash(without_edge_anchor)
    with pytest.raises(GraphError, match="静态实现关系缺少源码锚点"):
        validate_document(without_edge_anchor, schema())

    unknown_snapshot = copy.deepcopy(document)
    unknown_nodes = cast(list[JsonObject], cast(JsonObject, unknown_snapshot["graph"])["nodes"])
    unknown_nodes[-1]["snapshotId"] = "SNAPSHOT-UNKNOWN-CODE"
    refresh_hash(unknown_snapshot)
    with pytest.raises(GraphError, match="引用了不存在的代码快照"):
        validate_document(unknown_snapshot, schema())

    reversed_trace = copy.deepcopy(document)
    trace_edges = cast(list[JsonObject], cast(JsonObject, reversed_trace["graph"])["edges"])
    trace = next(
        edge for edge in trace_edges if edge["id"] == "EDGE-DESIGN-VALIDATOR-IMPLEMENTED-BY-APPROVE"
    )
    trace["sourceId"], trace["targetId"] = trace["targetId"], trace["sourceId"]
    refresh_hash(reversed_trace)
    with pytest.raises(GraphError, match="跨层追踪方向无效"):
        validate_document(reversed_trace, schema())
    assert scenario_id.startswith("SCN-")
