from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import cast

from app.config import ROOT
from app.graph import JsonObject, graph_hash, load_object, requirement_context
from app.runner import Runner
from app.service import ReviewService
from app.store import Store


class 捕获投影运行器:
    def __init__(self) -> None:
        self.dot = ""

    async def dependency_status(self) -> JsonObject:
        return {}

    async def render(self, dot_source: str) -> str:
        self.dot = dot_source
        return "<svg />"


def _document(direct: bool) -> JsonObject:
    document = copy.deepcopy(load_object(ROOT / "model" / "review-tool.json"))
    graph = cast(JsonObject, document["graph"])
    nodes = cast(list[JsonObject], graph["nodes"])
    edges = cast(list[JsonObject], graph["edges"])
    snapshot = cast(list[JsonObject], graph["codeSnapshots"])[0]
    snapshot_id = str(snapshot["id"])
    extractor_id = str(cast(list[JsonObject], snapshot["extractors"])[0]["id"])
    anchor: JsonObject = {
        "snapshotId": snapshot_id,
        "path": "app/example.py",
        "range": {
            "start": {"line": 1, "column": 0},
            "end": {"line": 2, "column": 1},
        },
        "extractorId": extractor_id,
        "fingerprint": "a" * 64,
    }
    focus_id = "SCN-TEST-CODE-PRECEDENCE"
    nodes.extend(
        [
            {
                "id": focus_id,
                "layer": "requirement",
                "kind": "Scenario",
                "title": "测试代码链优先级",
                "summary": "测试直接函数映射与模块兜底。",
                "source": "DECLARED",
                "details": {
                    "given": ["统一图存在代码映射"],
                    "when": ["用户展开代码链"],
                    "then": ["系统投影准确函数"],
                },
            },
            {
                "id": "DESIGN-TEST-CODE-PRECEDENCE",
                "layer": "design",
                "kind": "Component",
                "title": "测试代码链投影器",
                "summary": "测试用技术设计节点。",
                "source": "INFERRED",
                "details": {},
            },
            {
                "id": "IMPL-MODULE-TEST",
                "layer": "implementation",
                "kind": "Module",
                "title": "app/example.py",
                "summary": "测试模块。",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "anchors": [anchor],
                "details": {"path": "app/example.py"},
            },
            *(
                {
                    "id": node_id,
                    "layer": "implementation",
                    "kind": "Symbol",
                    "title": title,
                    "summary": "测试函数。",
                    "source": "DERIVED",
                    "snapshotId": snapshot_id,
                    "anchors": [anchor],
                    "details": {"qualifiedName": title},
                }
                for node_id, title in (
                    ("IMPL-SYMBOL-DIRECT", "direct"),
                    ("IMPL-SYMBOL-CALLED", "called"),
                    ("IMPL-SYMBOL-UNRELATED", "unrelated"),
                )
            ),
        ]
    )
    edges.extend(
        [
            {
                "id": "EDGE-TEST-CODE-PRECEDENCE-REALIZED",
                "sourceId": focus_id,
                "targetId": "DESIGN-TEST-CODE-PRECEDENCE",
                "kind": "realized_by",
                "source": "INFERRED",
            },
            *(
                {
                    "id": f"EDGE-MODULE-{node_id}",
                    "sourceId": "IMPL-MODULE-TEST",
                    "targetId": node_id,
                    "kind": "contains",
                    "source": "DERIVED",
                    "snapshotId": snapshot_id,
                }
                for node_id in (
                    "IMPL-SYMBOL-DIRECT",
                    "IMPL-SYMBOL-CALLED",
                    "IMPL-SYMBOL-UNRELATED",
                )
            ),
            {
                "id": "EDGE-DIRECT-CALLS-CALLED",
                "sourceId": "IMPL-SYMBOL-DIRECT",
                "targetId": "IMPL-SYMBOL-CALLED",
                "kind": "calls",
                "source": "DERIVED",
                "snapshotId": snapshot_id,
                "anchors": [anchor],
            },
            {
                "id": "EDGE-FOCUS-IMPLEMENTS",
                "sourceId": "DESIGN-TEST-CODE-PRECEDENCE",
                "targetId": "IMPL-SYMBOL-DIRECT" if direct else "IMPL-MODULE-TEST",
                "kind": "implemented_by",
                "source": "INFERRED",
            },
        ]
    )
    revision = cast(JsonObject, document["revision"])
    revision["contentHash"] = graph_hash(graph)
    revision["status"] = "APPROVED"
    return document


async def _projection(tmp_path: Path, direct: bool) -> str:
    document = _document(direct)
    database = tmp_path / ("direct.sqlite3" if direct else "fallback.sqlite3")
    store = Store(database, load_object(ROOT / "model" / "graph.schema.json"))
    runner = 捕获投影运行器()
    service = ReviewService(store, cast(Runner, runner), document)
    await service.initialize()
    await service.graph_svg(
        str(cast(JsonObject, document["revision"])["id"]),
        {"implementation"},
        "SCN-TEST-CODE-PRECEDENCE",
    )
    context = requirement_context(document, "SCN-TEST-CODE-PRECEDENCE")
    names = {item["qualifiedName"] for item in cast(list[JsonObject], context["codePath"])}
    if direct:
        assert names == {"direct", "called"}
    else:
        assert names == {"direct", "called", "unrelated"}
    await service.close()
    return runner.dot


def test_SCN_REQUIREMENT_CODE_DRILLDOWN_001_直接映射排除同模块无关函数(tmp_path: Path) -> None:
    dot = asyncio.run(_projection(tmp_path, True))
    assert "IMPL-SYMBOL-DIRECT" in dot
    assert "IMPL-SYMBOL-CALLED" in dot
    assert "IMPL-SYMBOL-UNRELATED" not in dot


def test_SCN_REQUIREMENT_CODE_DRILLDOWN_001_无直接映射时保留模块兜底(tmp_path: Path) -> None:
    dot = asyncio.run(_projection(tmp_path, False))
    assert "IMPL-SYMBOL-DIRECT" in dot
    assert "IMPL-SYMBOL-CALLED" in dot
    assert "IMPL-SYMBOL-UNRELATED" in dot
