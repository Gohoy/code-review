from pathlib import Path
from typing import cast

from app.graph import JsonObject, code_index_diff
from app.indexer import index_repository


def _symbol(node_id: str, qualified_name: str) -> JsonObject:
    return {
        "id": node_id,
        "kind": "Symbol",
        "layer": "implementation",
        "details": {"qualifiedName": qualified_name},
        "anchors": [{"path": "service.py"}],
    }


def test_SCN_REPOSITORY_BASELINE_001_刷新按唯一源码锚点迁移直接映射(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    source_id = "DESIGN-COMPONENT-SERVICE"
    graph: JsonObject = {
        "nodes": [_symbol("OLD-RUN", "run")],
        "edges": [
            {
                "id": "EDGE-DESIGN-OLD-RUN",
                "sourceId": source_id,
                "targetId": "OLD-RUN",
                "kind": "implemented_by",
            }
        ],
    }

    result = index_repository(tmp_path, ["service.py"], "1" * 40, "2" * 40, graph)

    function = cast(list[JsonObject], result["functions"])[0]
    assert function["mappedNodeIds"] == [source_id]
    assert result["migrationGaps"] == []
    diff = code_index_diff({"revision": {"id": "REV-OLD"}, "graph": graph}, result)
    assert any(
        edge.get("sourceId") == source_id
        and edge.get("targetId") == function["id"]
        and edge.get("kind") == "implemented_by"
        for edge in cast(list[JsonObject], diff["upsertEdges"])
    )


def test_SCN_REPOSITORY_BASELINE_001_刷新保留歧义和无匹配缺口(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    graph: JsonObject = {
        "nodes": [
            _symbol("OLD-RUN-A", "run"),
            _symbol("OLD-RUN-B", "run"),
            _symbol("OLD-MISSING", "missing"),
        ],
        "edges": [
            {
                "id": f"EDGE-DESIGN-{node_id}",
                "sourceId": "DESIGN-COMPONENT-SERVICE",
                "targetId": node_id,
                "kind": "implemented_by",
            }
            for node_id in ("OLD-RUN-A", "OLD-MISSING")
        ],
    }

    result = index_repository(tmp_path, ["service.py"], "1" * 40, "2" * 40, graph)

    function = cast(list[JsonObject], result["functions"])[0]
    gaps = cast(list[JsonObject], result["migrationGaps"])
    assert function["mappedNodeIds"] == []
    assert gaps == [
        {
            "path": "service.py",
            "qualifiedName": "run",
            "reason": "AMBIGUOUS_ANCHOR",
            "candidateNodeIds": ["OLD-RUN-A", "OLD-RUN-B"],
        },
        {
            "path": "service.py",
            "qualifiedName": "missing",
            "reason": "NO_MATCH",
            "candidateNodeIds": [],
        },
    ]
