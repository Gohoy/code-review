from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import cast

from app.bootstrap import load_store
from app.config import ROOT, Settings
from app.graph import JsonObject
from app.indexer import index_repository
from app.store import Store


def _settings(tmp_path: Path, repository: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8417,
        data_dir=tmp_path / "data",
        repository=repository,
        worktree_root=tmp_path / "worktree",
        web_dir=ROOT / "prototype" / "dist" / "client",
        model_path=ROOT / "model" / "review-tool.json",
        revision_dir=ROOT / "model" / "revision",
        graph_schema_path=ROOT / "model" / "graph.schema.json",
        agent_result_schema_path=ROOT / "app" / "schema" / "agent-result.schema.json",
        prompt_dir=ROOT / "prompt",
    )


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _create_repository(repository: Path) -> None:
    repository.mkdir()
    (repository / "README.md").write_text("# 订单服务\n用户可以提交订单。\n", encoding="utf-8")
    (repository / "AGENTS.md").write_text("保持订单校验行为稳定。\n", encoding="utf-8")
    (repository / "route.py").write_text(
        "from core import validate_order\n\n"
        "def submit_order(order):\n"
        "    return validate_order(order)\n",
        encoding="utf-8",
    )
    (repository / "core.py").write_text(
        "def validate_order(order):\n    return {'id': order['id']}\n",
        encoding="utf-8",
    )
    (repository / "test_route.py").write_text(
        "from route import submit_order\n\n"
        "def test_submit_order():\n"
        "    assert submit_order({'id': 1})['id'] == 1\n",
        encoding="utf-8",
    )
    _git(repository, "init", "-q")
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=测试",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "初始化",
    )


def _candidate(tmp_path: Path) -> tuple[Store, JsonObject, str]:
    repository = tmp_path / "order-service"
    _create_repository(repository)
    store, seed, history = load_store(_settings(tmp_path, repository))
    store.initialize(seed, history)
    agent_run_id, current = store.begin_baseline_agent()
    graph = cast(JsonObject, current["graph"])
    index = index_repository(
        repository,
        ["route.py", "core.py", "test_route.py"],
        _git(repository, "rev-parse", "HEAD"),
        _git(repository, "rev-parse", "HEAD^{tree}"),
        graph,
    )
    indexed = store.sync_code_index(agent_run_id, index)
    indexed_graph = cast(JsonObject, indexed["graph"])
    nodes = cast(list[JsonObject], indexed_graph["nodes"])
    modules = [node for node in nodes if node.get("kind") == "Module"]
    symbols = [node for node in nodes if node.get("kind") == "Symbol"]
    assert modules and symbols
    revision = cast(JsonObject, indexed["revision"])
    candidate = store.create_candidate(
        agent_run_id,
        {
            "baseRevisionId": revision["id"],
            "deleteNodeIds": ["QUESTION-REPOSITORY-BASELINE"],
            "deleteEdgeIds": ["EDGE-REPOSITORY-BASELINE-BLOCKS"],
            "upsertNodes": [
                {
                    "id": "ACTOR-REPOSITORY-USER",
                    "layer": "requirement",
                    "kind": "Actor",
                    "title": "下单用户",
                    "summary": "提交订单的用户。",
                    "source": "INFERRED",
                    "details": {},
                },
                {
                    "id": "FLOW-SUBMIT-ORDER",
                    "layer": "requirement",
                    "kind": "Flow",
                    "title": "提交订单",
                    "summary": "接收并校验订单。",
                    "source": "INFERRED",
                    "details": {},
                },
                {
                    "id": "OUTCOME-ORDER-ACCEPTED",
                    "layer": "requirement",
                    "kind": "Outcome",
                    "title": "订单已接受",
                    "summary": "有效订单返回订单标识。",
                    "source": "INFERRED",
                    "details": {},
                },
                {
                    "id": "SCN-ORDER-SUBMIT-001",
                    "layer": "requirement",
                    "kind": "Scenario",
                    "title": "提交有效订单",
                    "summary": "用户提交有效订单并获得标识。",
                    "source": "INFERRED",
                    "details": {
                        "given": ["用户具有有效订单"],
                        "when": ["用户提交订单"],
                        "then": ["系统返回订单标识"],
                    },
                },
                {
                    "id": "DESIGN-ORDER-SERVICE",
                    "layer": "design",
                    "kind": "Component",
                    "title": "订单服务",
                    "summary": "订单入口调用核心校验。",
                    "source": "INFERRED",
                    "details": {},
                },
            ],
            "upsertEdges": [
                {
                    "id": "EDGE-ACTOR-INITIATES-ORDER",
                    "sourceId": "ACTOR-REPOSITORY-USER",
                    "targetId": "FLOW-SUBMIT-ORDER",
                    "kind": "initiates",
                    "source": "INFERRED",
                },
                {
                    "id": "EDGE-ORDER-ACCEPTED-BRANCH",
                    "sourceId": "FLOW-SUBMIT-ORDER",
                    "targetId": "OUTCOME-ORDER-ACCEPTED",
                    "kind": "branch",
                    "source": "INFERRED",
                },
                {
                    "id": "EDGE-ORDER-SCENARIO-REALIZED",
                    "sourceId": "SCN-ORDER-SUBMIT-001",
                    "targetId": "DESIGN-ORDER-SERVICE",
                    "kind": "realized_by",
                    "source": "INFERRED",
                },
                *[
                    {
                        "id": f"EDGE-ORDER-MODULE-{number}",
                        "sourceId": "DESIGN-ORDER-SERVICE",
                        "targetId": module["id"],
                        "kind": "implemented_by",
                        "source": "INFERRED",
                    }
                    for number, module in enumerate(modules, 1)
                ],
                {
                    "id": "EDGE-ORDER-KEY-SYMBOL",
                    "sourceId": "DESIGN-ORDER-SERVICE",
                    "targetId": symbols[0]["id"],
                    "kind": "implemented_by",
                    "source": "INFERRED",
                },
            ],
        },
    )
    return store, candidate, agent_run_id


def test_SCN_EXTERNAL_REPOSITORY_BASELINE_001_SCN_REPOSITORY_BASELINE_001_中立初始化(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "order-service"
    _create_repository(repository)
    store, seed, history = load_store(_settings(tmp_path, repository))
    assert history == ()
    text = str(seed).lower()
    for forbidden in ("sdbp-review", "统一图", "mcp", "codex", "worktree"):
        assert forbidden not in text
    assert cast(JsonObject, seed["revision"])["approvable"] is False
    store.initialize(seed)
    errors = store.candidate_approval_errors(seed)
    assert "必须删除仓库中立占位并建立目标仓库自己的行为基线" in errors


def test_SCN_EXTERNAL_REPOSITORY_BASELINE_001_SCN_REPOSITORY_BASELINE_001_审批链(
    tmp_path: Path,
) -> None:
    store, candidate, agent_run_id = _candidate(tmp_path)
    assert store.candidate_approval_errors(candidate) == []
    assert cast(JsonObject, candidate["revision"])["approvable"] is True

    def errors_after(change: str) -> list[str]:
        broken = copy.deepcopy(candidate)
        graph = cast(JsonObject, broken["graph"])
        nodes = cast(list[JsonObject], graph["nodes"])
        edges = cast(list[JsonObject], graph["edges"])
        if change == "scenario":
            graph["nodes"] = [node for node in nodes if node.get("kind") != "Scenario"]
            graph["edges"] = [edge for edge in edges if edge.get("kind") != "realized_by"]
        elif change == "actor":
            graph["nodes"] = [node for node in nodes if node.get("kind") != "Actor"]
        elif change == "flow":
            graph["nodes"] = [node for node in nodes if node.get("kind") != "Flow"]
        elif change == "branch":
            graph["edges"] = [edge for edge in edges if edge.get("kind") != "branch"]
        elif change == "realized_by":
            graph["edges"] = [edge for edge in edges if edge.get("kind") != "realized_by"]
        elif change == "module":
            module_id = next(node["id"] for node in nodes if node.get("kind") == "Module")
            graph["edges"] = [
                edge
                for edge in edges
                if not (edge.get("kind") == "implemented_by" and edge.get("targetId") == module_id)
            ]
        elif change == "symbol":
            symbol_ids = {node["id"] for node in nodes if node.get("kind") == "Symbol"}
            graph["edges"] = [
                edge
                for edge in edges
                if not (edge.get("kind") == "implemented_by" and edge.get("targetId") in symbol_ids)
            ]
        elif change == "fallback":
            next(node for node in nodes if node["id"] == "DESIGN-ORDER-SERVICE")["title"] = "杂项"
        elif change == "unresolved":
            nodes.append(
                {
                    "id": "QUESTION-ORDER-MEANING",
                    "layer": "requirement",
                    "kind": "Question",
                    "title": "订单含义待确认",
                    "summary": "仓库证据不足。",
                    "source": "UNRESOLVED",
                    "details": {},
                }
            )
            edges.append(
                {
                    "id": "EDGE-ORDER-QUESTION-BLOCKS",
                    "sourceId": "QUESTION-ORDER-MEANING",
                    "targetId": "FLOW-SUBMIT-ORDER",
                    "kind": "blocks",
                    "source": "INFERRED",
                }
            )
        return store.candidate_approval_errors(broken)

    assert "外部仓库基线至少需要一个包含 Given/When/Then 的 Scenario" in errors_after("scenario")
    assert "外部仓库基线缺少目标用户 Actor" in errors_after("actor")
    assert "外部仓库基线缺少用户主流程" in errors_after("flow")
    assert "外部仓库基线缺少现实分支" in errors_after("branch")
    assert (
        "拥有源码 Module 的技术设计必须由 Scenario 通过 realized_by 追溯：DESIGN-ORDER-SERVICE"
    ) in errors_after("realized_by")
    assert any("仍有未归属 Module" in error for error in errors_after("module"))
    assert "外部仓库基线至少需要一个关键 Symbol 直接 implemented_by" in errors_after("symbol")
    assert any(
        "不得使用" in error and "DESIGN-ORDER-SERVICE" in error
        for error in errors_after("fallback")
    )
    unresolved_errors = errors_after("unresolved")
    assert "仍有待确认节点：订单含义待确认" in unresolved_errors
    assert not any("必须通过 blocks" in error for error in unresolved_errors)

    revision = cast(JsonObject, candidate["revision"])
    store.finish_agent(
        agent_run_id,
        {"status": "AWAITING_APPROVAL", "reply": "等待批准", "focusNodeIds": []},
    )
    run_id, _, _ = store.approve_and_create_run(str(revision["id"]), str(revision["contentHash"]))
    assert run_id.startswith("RUN-")


def test_SCN_EXTERNAL_REPOSITORY_BASELINE_001_SCN_REPOSITORY_BASELINE_001_Prompt契约() -> None:
    prompt = (ROOT / "prompt" / "repository-baseline.md").read_text(encoding="utf-8")
    for expected in (
        "repository-baseline@4.0.0",
        "README",
        "AGENTS",
        "用户入口",
        "路由",
        "核心代码",
        "测试",
        "UNRESOLVED",
        "blocks",
        "关键 Symbol",
        "sdbp-review",
    ):
        assert expected in prompt
