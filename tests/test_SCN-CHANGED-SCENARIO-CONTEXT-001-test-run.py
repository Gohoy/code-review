from __future__ import annotations

import asyncio
from pathlib import Path

from app import mcp_server
from app.graph import JsonObject


def test_SCN_CHANGED_SCENARIO_CONTEXT_001_固定测试仅验证本轮变化场景(
    monkeypatch: object, tmp_path: Path
) -> None:
    base: JsonObject = {
        "revision": {"id": "REV-BASE"},
        "graph": {
            "nodes": [
                {"id": "SCN-CHANGED", "kind": "Scenario", "title": "旧场景"},
                {"id": "SCN-UNCHANGED", "kind": "Scenario", "title": "不变场景"},
            ],
            "edges": [],
        },
    }
    current: JsonObject = {
        "revision": {"id": "REV-CURRENT", "baseRevisionId": "REV-BASE"},
        "graph": {
            "nodes": [
                {"id": "SCN-CHANGED", "kind": "Scenario", "title": "新场景"},
                {"id": "SCN-UNCHANGED", "kind": "Scenario", "title": "不变场景"},
            ],
            "edges": [],
        },
    }
    captured: list[frozenset[str]] = []

    class Store:
        def agent(self, _: str) -> JsonObject:
            return {"task": "IMPLEMENTATION"}

        def begin_tool(self, *_: object) -> str:
            return "TOOL-TEST"

        def finish_tool(self, *_: object) -> None:
            return None

        def begin_test(self, _: str) -> Path:
            return tmp_path

        def state(self) -> JsonObject:
            return {"revision": current, "baseRevision": base}

        def finish_test(self, *_: object) -> None:
            return None

    class Runner:
        async def verify(self, _: Path, scenario_ids: frozenset[str]) -> str:
            captured.append(scenario_ids)
            return "项目固定测试全部通过"

    monkeypatch.setattr(mcp_server, "store", Store())  # type: ignore[attr-defined]
    monkeypatch.setattr(mcp_server, "runner", Runner())  # type: ignore[attr-defined]
    monkeypatch.setattr(mcp_server, "agent_run_id", "AGENT-TEST")  # type: ignore[attr-defined]
    monkeypatch.setattr(mcp_server, "implementation_run_id", "RUN-TEST")  # type: ignore[attr-defined]

    asyncio.run(mcp_server.test_run())

    assert captured == [frozenset({"SCN-CHANGED"})]
