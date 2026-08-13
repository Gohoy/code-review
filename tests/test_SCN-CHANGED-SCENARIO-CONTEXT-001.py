from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import cast

from app.graph import JsonObject
from app.prompt import Prompt
from app.service import ReviewService


class _差异仓库:
    def __init__(self, current: JsonObject, base: JsonObject) -> None:
        self.current = current
        self.base = base

    def state(self) -> JsonObject:
        return {"revision": self.current, "baseRevision": self.base}

    def approve_and_create_run(
        self, revision_id: str, content_hash: str
    ) -> tuple[str, str, JsonObject]:
        return "RUN-TEST", "AGENT-IMPLEMENTATION", self.current

    def begin_review_agent(self, run_id: str) -> tuple[str, JsonObject]:
        return "AGENT-REVIEW", self.current


class _记录上下文服务(ReviewService):
    def __init__(self, store: _差异仓库) -> None:
        super().__init__(cast(object, store), cast(object, None), {})
        self.contexts: list[JsonObject] = []

    async def _record_prompt(self, agent_run_id: str, task: str, context: JsonObject) -> Prompt:
        self.contexts.append(context)
        return Prompt(task, "test", task, task)

    def _start(self, coroutine: Coroutine[object, object, None]) -> None:
        coroutine.close()

    async def _run_agent(
        self,
        task: str,
        agent_run_id: str,
        prompt: Prompt,
        implementation_run_id: str | None = None,
        worktree: Path | None = None,
    ) -> None:
        return None


def test_SCN_CHANGED_SCENARIO_CONTEXT_001_实现与Review注入相同稳定差异清单(
    tmp_path: Path,
) -> None:
    base: JsonObject = {
        "revision": {"id": "REV-BASE"},
        "graph": {
            "nodes": [
                {"id": "SCN-Z", "kind": "Scenario", "title": "旧场景"},
                {"id": "DESIGN-SAME", "kind": "Component", "title": "不变设计"},
            ],
            "edges": [],
        },
    }
    current: JsonObject = {
        "revision": {
            "id": "REV-CURRENT",
            "baseRevisionId": "REV-BASE",
            "contentHash": "content-hash",
        },
        "graph": {
            "nodes": [
                {"id": "SCN-Z", "kind": "Scenario", "title": "更新场景"},
                {"id": "DESIGN-SAME", "kind": "Component", "title": "不变设计"},
                {"id": "SCN-A", "kind": "Scenario", "title": "新增场景"},
            ],
            "edges": [
                {
                    "id": "EDGE-Z",
                    "kind": "realized_by",
                    "sourceId": "SCN-Z",
                    "targetId": "DESIGN-SAME",
                },
                {
                    "id": "EDGE-A",
                    "kind": "realized_by",
                    "sourceId": "SCN-A",
                    "targetId": "DESIGN-SAME",
                },
            ],
        },
    }
    service = _记录上下文服务(_差异仓库(current, base))

    async def exercise() -> None:
        await service.approve_and_start("REV-CURRENT", "content-hash")
        await service._review("RUN-TEST", {"worktree": str(tmp_path)})

    asyncio.run(exercise())

    implementation, semantic_review = service.contexts
    expected = {
        "changedNodeIds": ["SCN-A", "SCN-Z"],
        "changedEdgeIds": ["EDGE-A", "EDGE-Z"],
        "changedScenarioIds": ["SCN-A", "SCN-Z"],
    }
    for key, value in expected.items():
        assert implementation[key] == value
        assert semantic_review[key] == value
