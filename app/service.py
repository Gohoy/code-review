from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from pathlib import Path
from typing import cast

from app.graph import (
    JsonObject,
    approval_errors,
    changed_ids,
    requirement_context,
    to_dot,
)
from app.prompt import Prompt
from app.runner import Runner, prompt_input_hash
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

    async def requirement_context(self, focus_id: str) -> JsonObject:
        state = await asyncio.to_thread(self.store.state)
        return requirement_context(cast(JsonObject, state["revision"]), focus_id)

    async def submit_message(self, content: str) -> str:
        agent_run_id, document, messages = await asyncio.to_thread(self.store.begin_agent, content)
        revision = cast(JsonObject, document["revision"])
        context: JsonObject = {
            "agentRunId": agent_run_id,
            "task": "REQUIREMENT_CHANGE",
            "baseRevisionId": revision["id"],
            "conversation": messages,
            "expectedFinalStatus": ["AWAITING_APPROVAL", "NEEDS_INPUT"],
        }
        prompt = await self._record_prompt(agent_run_id, "REQUIREMENT_CHANGE", context)
        self._start(self._run_agent("REQUIREMENT_CHANGE", agent_run_id, prompt))
        return agent_run_id

    async def approve_and_start(self, revision_id: str, content_hash: str) -> str:
        run_id, agent_run_id, document = await asyncio.to_thread(
            self.store.approve_and_create_run, revision_id, content_hash
        )
        revision = cast(JsonObject, document["revision"])
        context: JsonObject = {
            "agentRunId": agent_run_id,
            "implementationRunId": run_id,
            "task": "IMPLEMENTATION",
            "approvedRevisionId": revision["id"],
            "approvedContentHash": revision["contentHash"],
            "expectedFinalStatus": ["COMPLETED", "NEEDS_INPUT"],
        }
        prompt = await self._record_prompt(agent_run_id, "IMPLEMENTATION", context)
        self._start(self._run_agent("IMPLEMENTATION", agent_run_id, prompt, run_id))
        return run_id

    async def _run_agent(
        self,
        task: str,
        agent_run_id: str,
        prompt: Prompt,
        implementation_run_id: str | None = None,
        worktree: Path | None = None,
    ) -> None:
        try:
            result = await self.runner.agent(
                task,
                prompt,
                agent_run_id,
                implementation_run_id,
                worktree,
            )
            await asyncio.to_thread(self.store.finish_agent, agent_run_id, result)
            if task == "IMPLEMENTATION" and result.get("status") == "COMPLETED":
                if implementation_run_id is None:
                    raise ValueError("实现 Agent 缺少开发运行 ID")
                run = await asyncio.to_thread(self.store.implementation_run, implementation_run_id)
                if run["status"] != "VERIFIED":
                    raise ValueError("实现 Agent 未通过 MCP 完成固定测试")
                await self._review(implementation_run_id, run)
            if task == "SEMANTIC_REVIEW" and result.get("status") == "COMPLETED":
                if implementation_run_id is None:
                    raise ValueError("Review Agent 缺少开发运行 ID")
                run = await asyncio.to_thread(self.store.implementation_run, implementation_run_id)
                if run["status"] != "COMPLETED":
                    raise ValueError("Review Agent 未通过 MCP 完成语义 Review 和合并")
        except Exception as error:
            logger.exception("Agent 运行失败")
            try:
                agent = await asyncio.to_thread(self.store.agent, agent_run_id)
                if agent["status"] == "RUNNING":
                    await asyncio.to_thread(self.store.fail_agent, agent_run_id, str(error))
                if implementation_run_id is not None:
                    run = await asyncio.to_thread(
                        self.store.implementation_run, implementation_run_id
                    )
                    if run["status"] not in {
                        "COMPLETED",
                        "FAILED",
                        "NEEDS_INPUT",
                        "BLOCKED",
                    }:
                        await asyncio.to_thread(
                            self.store.fail_run, implementation_run_id, str(error)
                        )
            except Exception:
                logger.exception("记录 Agent 失败状态时再次失败")

    async def _review(self, run_id: str, run: JsonObject) -> None:
        agent_run_id, document = await asyncio.to_thread(self.store.begin_review_agent, run_id)
        worktree_value = run.get("worktree")
        if not isinstance(worktree_value, str):
            raise ValueError("语义 Review 缺少开发 worktree")
        revision = cast(JsonObject, document["revision"])
        context: JsonObject = {
            "agentRunId": agent_run_id,
            "implementationRunId": run_id,
            "task": "SEMANTIC_REVIEW",
            "approvedRevisionId": revision["id"],
            "approvedContentHash": revision["contentHash"],
            "changeResource": f"change://{run_id}",
            "expectedFinalStatus": ["COMPLETED", "BLOCKED"],
        }
        prompt = await self._record_prompt(agent_run_id, "SEMANTIC_REVIEW", context)
        await self._run_agent(
            "SEMANTIC_REVIEW",
            agent_run_id,
            prompt,
            run_id,
            Path(worktree_value),
        )

    async def _record_prompt(self, agent_run_id: str, task: str, context: JsonObject) -> Prompt:
        prompt = self.runner.prompt(task, context)
        await asyncio.to_thread(
            self.store.record_agent_prompt,
            agent_run_id,
            prompt.id,
            prompt.version,
            prompt.hash,
            prompt_input_hash(context),
        )
        return prompt

    def _start(self, coroutine: Coroutine[object, object, None]) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
