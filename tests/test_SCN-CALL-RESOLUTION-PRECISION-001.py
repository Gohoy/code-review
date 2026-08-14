from __future__ import annotations

from pathlib import Path
from typing import cast

from app.graph import JsonObject, code_index_diff, with_code_snapshot
from app.indexer import index_repository


def _base(indexed: JsonObject) -> JsonObject:
    return with_code_snapshot(
        {"revision": {"id": "REV-BASE"}, "graph": {"nodes": [], "edges": []}},
        cast(JsonObject, indexed["snapshot"]),
    )


def test_SCN_CALL_RESOLUTION_PRECISION_001_Python调用只按静态唯一证据连边(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """def top_level_only(): pass

class Outer:
    class Inner:
        def self_only(self): pass
        @classmethod
        def cls_only(cls): pass
        def explicit_only(self): pass
        def self_source(self):
            self.self_only()
        @classmethod
        def cls_source(cls):
            cls.cls_only()
        def explicit_source(self):
            Outer.Inner.explicit_only()
        def top_source(self):
            top_level_only()
        def unresolved_source(self):
            service.pop()
            redis.eval()
            ambiguous()

def local_owner():
    def local_only(): pass
    def local_source():
        local_only()
    local_source()
""",
        encoding="utf-8",
    )
    (tmp_path / "other.py").write_text("def ambiguous(): pass\n", encoding="utf-8")
    (tmp_path / "another.py").write_text("def ambiguous(): pass\n", encoding="utf-8")
    (tmp_path / "tests_fake.py").write_text(
        "class FakeService:\n    def pop(self): pass\n    def eval(self): pass\n",
        encoding="utf-8",
    )
    indexed = index_repository(
        tmp_path,
        ["service.py", "other.py", "another.py", "tests_fake.py"],
        "1" * 40,
        "2" * 40,
        {"nodes": [], "edges": []},
    )
    graph = code_index_diff(_base(indexed), indexed)
    nodes = {node["id"]: node for node in cast(list[JsonObject], graph["upsertNodes"])}
    calls = {
        (nodes[edge["sourceId"]]["title"], nodes[edge["targetId"]]["title"])
        for edge in cast(list[JsonObject], graph["upsertEdges"])
        if edge["kind"] == "calls"
    }

    assert ("Outer.Inner.self_source", "Outer.Inner.self_only") in calls
    assert ("Outer.Inner.cls_source", "Outer.Inner.cls_only") in calls
    assert ("Outer.Inner.explicit_source", "Outer.Inner.explicit_only") in calls
    assert ("Outer.Inner.top_source", "top_level_only") in calls
    assert ("local_owner.local_source", "local_owner.local_only") in calls
    source = next(
        node for node in nodes.values() if node.get("title") == "Outer.Inner.unresolved_source"
    )
    assert {"service.pop", "redis.eval", "ambiguous"} <= set(source["details"]["unresolvedCalls"])
    assert not any(
        caller == "Outer.Inner.unresolved_source" and target.startswith("FakeService.")
        for caller, target in calls
    )


def test_SCN_CALL_RESOLUTION_PRECISION_001_JavaScript调用解析保持现状(tmp_path: Path) -> None:
    (tmp_path / "source.js").write_text("function source() { target(); }\n", encoding="utf-8")
    (tmp_path / "target.js").write_text("function target() {}\n", encoding="utf-8")
    indexed = index_repository(
        tmp_path,
        ["source.js", "target.js"],
        "1" * 40,
        "2" * 40,
        {"nodes": [], "edges": []},
    )
    graph = code_index_diff(_base(indexed), indexed)
    nodes = {node["id"]: node for node in cast(list[JsonObject], graph["upsertNodes"])}
    assert any(
        edge["kind"] == "calls"
        and nodes[edge["sourceId"]]["title"] == "source"
        and nodes[edge["targetId"]]["title"] == "target"
        for edge in cast(list[JsonObject], graph["upsertEdges"])
    )
