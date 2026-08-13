from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import cast

from app.graph import (
    JsonObject,
    apply_diff,
    approval_errors,
    changed_ids,
    to_dot,
)
from app.runner import Runner
from app.store import Store

logger = logging.getLogger(__name__)


class ReviewService:
    def __init__(
        self,
        store: Store,
        runner: Runner,
        seed: JsonObject,
        history: tuple[JsonObject, ...] = (),
    ) -> None:
        self.store = store
        self.runner = runner
        self.seed = seed
        self.history = history
        self.dependencies: JsonObject = {}
        self.tasks: set[asyncio.Task[None]] = set()

    async def initialize(self) -> None:
        await asyncio.to_thread(self.store.initialize, self.seed, self.history)
        self.dependencies = await self.runner.dependency_status()

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def state(self) -> JsonObject:
        state = await asyncio.to_thread(self.store.state)
        document = cast(JsonObject, state["revision"])
        base_value = state.get("baseRevision")
        base = cast(JsonObject, base_value) if isinstance(base_value, dict) else None
        node_ids, edge_ids = changed_ids(base, document)
        graph = cast(JsonObject, document["graph"])
        nodes = cast(list[JsonObject], graph["nodes"])
        state["changedNodeIds"] = sorted(node_ids)
        state["changedEdgeIds"] = sorted(edge_ids)
        state["approvalErrors"] = approval_errors(document)
        state["layerCounts"] = {
            layer: sum(node.get("layer") == layer for node in nodes)
            for layer in ("requirement", "design", "implementation", "verification")
        }
        state["dependencies"] = self.dependencies
        return state

    async def graph_svg(self, layers: set[str], focus_id: str | None) -> str:
        state = await asyncio.to_thread(self.store.state)
        document = cast(JsonObject, state["revision"])
        base_value = state.get("baseRevision")
        base = cast(JsonObject, base_value) if isinstance(base_value, dict) else None
        node_ids, edge_ids = changed_ids(base, document)
        dot_source = to_dot(document, layers, node_ids, edge_ids, focus_id)
        return await self.runner.render(dot_source)

    async def submit_message(self, content: str) -> None:
        document, messages = await asyncio.to_thread(self.store.begin_modeling, content)
        self._start(self._model(document, messages))

    async def approve_and_start(self, revision_id: str, content_hash: str) -> str:
        run_id, document = await asyncio.to_thread(
            self.store.approve_and_create_run, revision_id, content_hash
        )
        self._start(self._implement(run_id, document))
        return run_id

    async def _model(self, document: JsonObject, messages: list[JsonObject]) -> None:
        revision = cast(JsonObject, document["revision"])
        base_revision_id = str(revision["id"])
        try:
            diff = await self.runner.model(document, messages)
            revision_id = await asyncio.to_thread(self.store.next_revision_id)
            candidate = apply_diff(document, diff, revision_id, self.store.graph_schema)
            reply = diff.get("reply")
            if not isinstance(reply, str):
                raise ValueError("AI 回复缺失")
            await asyncio.to_thread(
                self.store.complete_modeling, base_revision_id, candidate, reply
            )
        except Exception as error:
            logger.exception("需求建模失败")
            await asyncio.to_thread(self.store.fail_modeling, str(error))

    async def _implement(self, run_id: str, document: JsonObject) -> None:
        try:
            worktree = await self.runner.create_worktree(run_id)
            await asyncio.to_thread(self.store.start_run, run_id, worktree)
            result = await self.runner.implement(document, worktree)
            if result.get("status") != "COMPLETED":
                await asyncio.to_thread(self.store.finish_run, run_id, result)
                return
            implementation_summary = str(result["summary"])
            await asyncio.to_thread(
                self.store.update_run_progress, run_id, "VERIFYING", implementation_summary
            )
            verification_summary = await self.runner.verify(worktree)
            await asyncio.to_thread(
                self.store.update_run_progress, run_id, "MERGING", verification_summary
            )
            revision = cast(JsonObject, document["revision"])
            merge_summary = await self.runner.merge(worktree, str(revision["id"]))
            await asyncio.to_thread(
                self.store.finish_run,
                run_id,
                {
                    "status": "COMPLETED",
                    "summary": f"{implementation_summary}；{verification_summary}；{merge_summary}",
                    "question": "",
                },
            )
        except Exception as error:
            logger.exception("隔离开发失败")
            await asyncio.to_thread(self.store.fail_run, run_id, str(error))

    def _start(self, coroutine: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
