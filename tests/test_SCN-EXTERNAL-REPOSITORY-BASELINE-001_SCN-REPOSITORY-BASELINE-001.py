from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import cast

from app.bootstrap import load_store
from app.config import ROOT, Settings
from app.graph import JsonObject, graph_hash
from app.indexer import index_repository


def _settings(tmp_path: Path, repository: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8417,
        data_dir=tmp_path / "data",
        repository=repository,
        worktree_root=tmp_path / "worktree",
        web_dir=ROOT / "prototype/dist/client",
        model_path=ROOT / "model/review-tool.json",
        revision_dir=ROOT / "model/revision",
        graph_schema_path=ROOT / "model/graph.schema.json",
        agent_result_schema_path=ROOT / "app/schema/agent-result.schema.json",
        prompt_dir=ROOT / "prompt",
    )


def _external_repository(path: Path) -> None:
    path.mkdir()
    (path / "README.md").write_text("# Orders\nUsers submit orders.\n", encoding="utf-8")
    (path / "AGENTS.md").write_text("Keep the order route stable.\n", encoding="utf-8")
    (path / "service.py").write_text(
        "def submit_order(order):\n    return {'id': order['id']}\n", encoding="utf-8"
    )
    (path / "test_service.py").write_text(
        "from service import submit_order\n\n"
        "def test_order():\n"
        "    assert submit_order({'id': 1})['id'] == 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "seed",
        ],
        check=True,
    )


def _complete_document(indexed: JsonObject) -> JsonObject:
    graph = cast(JsonObject, copy.deepcopy(indexed["graph"]))
    nodes = cast(list[JsonObject], graph["nodes"])
    edges = cast(list[JsonObject], graph["edges"])
    nodes[:] = [node for node in nodes if node.get("id") != "QUESTION-REPOSITORY-BASELINE"]
    module = next(node for node in nodes if node.get("kind") == "Module")
    symbol = next(node for node in nodes if node.get("kind") == "Symbol")
    nodes.extend(
        [
            {
                "id": "ACTOR-CUSTOMER",
                "layer": "requirement",
                "kind": "Actor",
                "title": "下单用户",
                "summary": "提交订单的用户",
                "source": "INFERRED",
                "details": {},
            },
            {
                "id": "SCN-ORDER-SUBMIT",
                "layer": "requirement",
                "kind": "Scenario",
                "title": "提交订单",
                "summary": "用户提交有效订单",
                "source": "INFERRED",
                "details": {
                    "given": ["用户有有效订单"],
                    "when": ["提交订单"],
                    "then": ["返回订单标识"],
                },
            },
            {
                "id": "DESIGN-ORDER-SERVICE",
                "layer": "design",
                "kind": "Component",
                "title": "订单服务",
                "summary": "处理订单入口",
                "source": "INFERRED",
                "details": {},
            },
        ]
    )
    graph["entryNodeIds"] = ["ACTOR-CUSTOMER"]
    edges.extend(
        [
            {
                "id": "EDGE-SCN-DESIGN",
                "sourceId": "SCN-ORDER-SUBMIT",
                "targetId": "DESIGN-ORDER-SERVICE",
                "kind": "realized_by",
                "source": "INFERRED",
            },
            {
                "id": "EDGE-DESIGN-MODULE",
                "sourceId": "DESIGN-ORDER-SERVICE",
                "targetId": module["id"],
                "kind": "implemented_by",
                "source": "INFERRED",
            },
            {
                "id": "EDGE-DESIGN-SYMBOL",
                "sourceId": "DESIGN-ORDER-SERVICE",
                "targetId": symbol["id"],
                "kind": "implemented_by",
                "source": "INFERRED",
            },
        ]
    )
    return {
        "schemaVersion": 2,
        "modelId": "MODEL-ORDERS",
        "title": "Orders",
        "revision": {
            "id": "REV-ORDERS-001",
            "status": "CANDIDATE",
            "baseRevisionId": None,
            "contentHash": graph_hash(graph),
            "approvable": True,
        },
        "graph": graph,
    }


def test_SCN_EXTERNAL_REPOSITORY_BASELINE_001_SCN_REPOSITORY_BASELINE_001_a(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "orders"
    _external_repository(repository)
    store, seed, history = load_store(_settings(tmp_path, repository))
    assert history == ()
    assert "sdbp-review" not in str(seed).lower()
    assert seed["revision"]["approvable"] is False
    store.initialize(seed)
    assert "必须删除仓库中立占位" in "；".join(store.candidate_approval_errors(seed))
    index = index_repository(repository, ["service.py"], "1" * 40, "2" * 40, {"nodes": []})
    assert index["fileFacts"]
    assert index["functions"]


def test_SCN_EXTERNAL_REPOSITORY_BASELINE_001_SCN_REPOSITORY_BASELINE_001_b(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "orders"
    _external_repository(repository)
    index = index_repository(repository, ["service.py"], "1" * 40, "2" * 40, {"nodes": []})
    store, seed, _ = load_store(_settings(tmp_path, repository))
    store.initialize(seed)
    agent_run_id, _ = store.begin_baseline_agent()
    indexed = store.sync_code_index(agent_run_id, index)
    complete = _complete_document(indexed)
    semantic_nodes = [
        node for node in complete["graph"]["nodes"] if node.get("layer") != "implementation"
    ]
    semantic_edges = [
        edge for edge in complete["graph"]["edges"] if edge.get("source") == "INFERRED"
    ]
    document = store.create_candidate(
        agent_run_id,
        {
            "baseRevisionId": indexed["revision"]["id"],
            "upsertNodes": semantic_nodes,
            "deleteNodeIds": ["QUESTION-REPOSITORY-BASELINE"],
            "upsertEdges": semantic_edges,
            "deleteEdgeIds": [],
        },
    )
    assert store.candidate_approval_errors(document) == []

    def remove_scenarios(item: JsonObject) -> None:
        item["graph"]["nodes"] = [
            node for node in item["graph"]["nodes"] if node.get("kind") != "Scenario"
        ]

    def remove_realization(item: JsonObject) -> None:
        item["graph"]["edges"] = [
            edge for edge in item["graph"]["edges"] if edge.get("kind") != "realized_by"
        ]

    def remove_direct_symbol_ownership(item: JsonObject) -> None:
        symbol_ids = {node["id"] for node in item["graph"]["nodes"] if node.get("kind") == "Symbol"}
        item["graph"]["edges"] = [
            edge
            for edge in item["graph"]["edges"]
            if not (edge.get("kind") == "implemented_by" and edge.get("targetId") in symbol_ids)
        ]

    for mutation, expected in (
        (remove_scenarios, "至少需要一个"),
        (remove_realization, "场景尚未关联技术设计"),
        (remove_direct_symbol_ownership, "关键 Symbol"),
    ):
        broken = copy.deepcopy(document)
        mutation(broken)
        assert expected in "；".join(store.candidate_approval_errors(broken))


def test_SCN_EXTERNAL_REPOSITORY_BASELINE_001_SCN_REPOSITORY_BASELINE_001_c() -> None:
    prompt = (ROOT / "prompt/repository-baseline.md").read_text(encoding="utf-8")
    for text in (
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
        assert text in prompt
    app = (ROOT / "prototype/src/App.jsx").read_text(encoding="utf-8")
    baseline = app[app.index("async function startBaseline") : app.index("function activateNode")]
    assert 'setLayer("requirement")' in baseline
