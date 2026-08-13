from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import cast

from app.config import ROOT
from app.graph import JsonObject, graph_hash, load_object
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


async def _投影(tmp_path: Path, view_mode: str, focus_id: str | None = None) -> str:
    document = copy.deepcopy(load_object(ROOT / "model" / "review-tool.json"))
    graph = cast(JsonObject, document["graph"])
    nodes = cast(list[JsonObject], graph["nodes"])
    edges = cast(list[JsonObject], graph["edges"])
    nodes.extend(
        [
            {
                "id": "SCN-CHANGED-TEST",
                "layer": "requirement",
                "kind": "Scenario",
                "title": "变化需求",
                "summary": "当前变化需求。",
                "source": "DECLARED",
                "details": {"given": ["存在变化"], "when": ["查看图"], "then": ["显示变化"]},
            },
            {
                "id": "SCN-HISTORICAL-TEST",
                "layer": "requirement",
                "kind": "Scenario",
                "title": "历史需求",
                "summary": "无关历史需求。",
                "source": "DECLARED",
                "details": {"given": ["存在历史"], "when": ["查看图"], "then": ["按模式显示"]},
            },
        ]
    )
    edges.extend(
        [
            {
                "id": "EDGE-FLOW-CHANGED-TEST",
                "sourceId": "FLOW-REQUIREMENT-TO-DEVELOPMENT",
                "targetId": "SCN-CHANGED-TEST",
                "kind": "contains",
                "source": "INFERRED",
            },
            {
                "id": "EDGE-FLOW-HISTORICAL-TEST",
                "sourceId": "FLOW-REQUIREMENT-TO-DEVELOPMENT",
                "targetId": "SCN-HISTORICAL-TEST",
                "kind": "contains",
                "source": "INFERRED",
            },
        ]
    )
    revision = cast(JsonObject, document["revision"])
    revision["id"] = "REV-PROJECTION-CURRENT"
    revision["contentHash"] = graph_hash(graph)
    base = copy.deepcopy(document)
    base_graph = cast(JsonObject, base["graph"])
    base_graph["nodes"] = [
        node
        for node in cast(list[JsonObject], base_graph["nodes"])
        if node.get("id") != "SCN-CHANGED-TEST"
    ]
    base_graph["edges"] = [
        edge
        for edge in cast(list[JsonObject], base_graph["edges"])
        if edge.get("targetId") != "SCN-CHANGED-TEST"
    ]
    base_revision = cast(JsonObject, base["revision"])
    base_revision["id"] = "REV-PROJECTION-BASE"
    base_revision["contentHash"] = graph_hash(base_graph)
    revision["baseRevisionId"] = base_revision["id"]
    store = Store(
        tmp_path / f"{view_mode}-{focus_id}.sqlite3",
        load_object(ROOT / "model" / "graph.schema.json"),
    )
    runner = 捕获投影运行器()
    service = ReviewService(store, cast(Runner, runner), document, (base,))
    await service.initialize()
    await service.graph_svg(str(revision["id"]), {"requirement"}, focus_id, view_mode, "LR")
    await service.close()
    return runner.dot


def test_SCN_GRAPH_EXPLORE_001_默认排除历史分支且保留变化(tmp_path: Path) -> None:
    dot = asyncio.run(_投影(tmp_path, "changes"))
    assert "SCN-CHANGED-TEST" in dot
    assert "SCN-HISTORICAL-TEST" not in dot
    assert dot.count('class="node layer-requirement') == 11


def test_SCN_GRAPH_EXPLORE_001_上下文恢复历史且焦点展开分支(tmp_path: Path) -> None:
    context = asyncio.run(_投影(tmp_path, "context"))
    focused = asyncio.run(_投影(tmp_path, "changes", "SCN-HISTORICAL-TEST"))
    assert "SCN-HISTORICAL-TEST" in context
    assert "SCN-HISTORICAL-TEST" in focused
    assert "SCN-CHANGED-TEST" in focused
