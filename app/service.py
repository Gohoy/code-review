from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
from collections.abc import Coroutine
from pathlib import Path
from typing import cast

from app.graph import (
    GraphError,
    JsonObject,
    _mainline_ids,
    approval_errors,
    changed_ids,
    code_ownership,
    requirement_context,
    to_dot,
)
from app.prompt import Prompt
from app.runner import Runner, prompt_input_hash
from app.store import Store

logger = logging.getLogger(__name__)


def revision_change_context(base: JsonObject | None, document: JsonObject) -> JsonObject:
    """返回一个 revision 相对基线的稳定变化清单。"""
    node_ids, edge_ids = changed_ids(base, document)
    graph = cast(JsonObject, document["graph"])
    nodes = cast(list[JsonObject], graph["nodes"])
    return {
        "changedNodeIds": sorted(node_ids),
        "changedEdgeIds": sorted(edge_ids),
        "changedScenarioIds": sorted(
            str(node["id"])
            for node in nodes
            if node.get("id") in node_ids and node.get("kind") == "Scenario"
        ),
    }


def validation_context_hash(changed_scenario_ids: list[str]) -> str:
    payload = json.dumps(changed_scenario_ids, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


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
        terminal_worktrees = await asyncio.to_thread(self.store.terminal_worktrees)
        await asyncio.to_thread(self.store.initialize, self.seed, self.history)
        for run_id, worktree in terminal_worktrees:
            await self._cleanup_worktree(run_id, worktree)
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
        run = state.get("implementationRun")
        if isinstance(run, dict) and isinstance(run.get("id"), str):
            run_base, run_document = await asyncio.to_thread(
                self.store.run_revision_documents, str(run["id"])
            )
            state.update(revision_change_context(run_base, run_document))
        else:
            state.update(revision_change_context(base, document))
        graph = cast(JsonObject, document["graph"])
        nodes = cast(list[JsonObject], graph["nodes"])
        state["approvalErrors"] = approval_errors(document)
        state["layerCounts"] = {
            layer: sum(node.get("layer") == layer for node in nodes)
            for layer in ("requirement", "design", "implementation", "verification")
        }
        state["dependencies"] = self.dependencies
        functions = [
            node
            for node in nodes
            if node.get("layer") == "implementation" and node.get("kind") == "Symbol"
        ]
        ownership = code_ownership(document)
        coverage_values = [
            cast(JsonObject, node["details"]).get("coverage")
            for node in functions
            if isinstance(node.get("details"), dict)
        ]
        state["codeMetrics"] = {
            **ownership,
            "mappedFunctionCount": ownership["semanticallyOwnedFunctionCount"],
            "coveredFunctionCount": sum(
                isinstance(value, dict) and value.get("status") == "COVERED"
                for value in coverage_values
            ),
            "uncoveredFunctionCount": sum(
                isinstance(value, dict) and value.get("status") == "UNCOVERED"
                for value in coverage_values
            ),
            "coverageStatus": "OBSERVED"
            if any(
                isinstance(value, dict) and value.get("status") != "UNKNOWN"
                for value in coverage_values
            )
            else "UNKNOWN",
        }
        return state

    async def status(self) -> JsonObject:
        """返回周期轮询所需的轻量状态。"""
        state = await self.state()
        document = cast(JsonObject, state.pop("revision"))
        state.pop("baseRevision", None)
        state["revision"] = cast(JsonObject, document["revision"])
        return state

    async def revision(self, revision_id: str) -> JsonObject:
        state = await asyncio.to_thread(self.store.state)
        document = cast(JsonObject, state["revision"])
        revision = cast(JsonObject, document["revision"])
        if revision.get("id") != revision_id:
            raise GraphError("revision 已变化，请刷新状态后重试")
        immutable_document = copy.deepcopy(document)
        immutable_revision = cast(JsonObject, immutable_document["revision"])
        immutable_revision.pop("status", None)
        immutable_revision.pop("approvable", None)
        return immutable_document

    async def graph_svg(
        self,
        revision_id: str,
        layers: set[str],
        focus_id: str | None,
        view_mode: str = "context",
        direction: str = "LR",
    ) -> tuple[str, str]:
        if view_mode not in {"changes", "context"}:
            raise GraphError("viewMode 参数无效")
        if direction not in {"LR", "TB"}:
            raise GraphError("direction 参数无效")
        state = await asyncio.to_thread(self.store.state)
        document = cast(JsonObject, state["revision"])
        revision = cast(JsonObject, document["revision"])
        if revision.get("id") != revision_id:
            raise GraphError("revision 已变化，请刷新状态后重试")
        base_value = state.get("baseRevision")
        base = cast(JsonObject, base_value) if isinstance(base_value, dict) else None
        node_ids, edge_ids = changed_ids(base, document)
        projection = copy.deepcopy(document)
        graph = cast(JsonObject, projection["graph"])
        nodes = cast(list[JsonObject], graph["nodes"])
        edges = cast(list[JsonObject], graph["edges"])
        if view_mode == "changes" and "requirement" in layers:
            requirement_ids = {
                str(node["id"]) for node in nodes if node.get("layer") == "requirement"
            }
            retained_requirement_ids = _mainline_ids(graph, edges)
            retained_requirement_ids.update(node_ids & requirement_ids)
            if focus_id in requirement_ids:
                retained_requirement_ids.add(cast(str, focus_id))
                retained_requirement_ids.update(
                    str(edge[endpoint])
                    for edge in edges
                    for endpoint in ("sourceId", "targetId")
                    if edge.get("kind") in {"contains", "next", "branch", "produces", "guarded_by"}
                    and focus_id in {edge.get("sourceId"), edge.get("targetId")}
                    and edge.get(endpoint) in requirement_ids
                )
            graph["nodes"] = [
                node
                for node in nodes
                if node.get("layer") != "requirement" or node.get("id") in retained_requirement_ids
            ]
            retained_ids = {str(node["id"]) for node in cast(list[JsonObject], graph["nodes"])}
            graph["edges"] = [
                edge
                for edge in edges
                if edge.get("sourceId") in retained_ids and edge.get("targetId") in retained_ids
            ]
        if "implementation" in layers:
            nodes = cast(list[JsonObject], graph["nodes"])
            edges = cast(list[JsonObject], graph["edges"])
            nodes_by_id = {str(node["id"]): node for node in nodes}
            module_ids = {
                str(node["id"])
                for node in nodes
                if node.get("layer") == "implementation" and node.get("kind") == "Module"
            }
            expanded_modules: set[str] = set()
            direct_symbols: set[str] = set()
            if focus_id in module_ids:
                expanded_modules.add(cast(str, focus_id))
            elif focus_id:
                related = {focus_id}
                pending = [focus_id]
                while pending:
                    current = pending.pop()
                    for edge in edges:
                        if edge.get("sourceId") != current or edge.get("kind") not in {
                            "contains",
                            "realized_by",
                            "implemented_by",
                            "verified_by",
                        }:
                            continue
                        target_id = str(edge["targetId"])
                        target = nodes_by_id.get(target_id)
                        if (
                            edge.get("kind") == "implemented_by"
                            and target is not None
                            and target.get("kind") == "Symbol"
                        ):
                            direct_symbols.add(target_id)
                            continue
                        if target_id not in related:
                            related.add(target_id)
                            pending.append(target_id)
                if direct_symbols:
                    pending = list(direct_symbols)
                    while pending:
                        current = pending.pop()
                        for edge in edges:
                            if edge.get("sourceId") != current or edge.get("kind") not in {
                                "calls",
                                "reads",
                                "writes",
                                "invokes",
                            }:
                                continue
                            target_id = str(edge["targetId"])
                            if target_id not in direct_symbols:
                                direct_symbols.add(target_id)
                                pending.append(target_id)
                else:
                    expanded_modules = module_ids & related
            expanded_symbols = direct_symbols or {
                str(edge["targetId"])
                for edge in edges
                if edge.get("kind") == "contains" and edge.get("sourceId") in expanded_modules
            }
            graph["nodes"] = [
                node
                for node in nodes
                if node.get("layer") != "implementation"
                or node.get("kind") != "Symbol"
                or node.get("id") in expanded_symbols
            ]
            retained_ids = {str(node["id"]) for node in cast(list[JsonObject], graph["nodes"])}
            graph["edges"] = [
                edge
                for edge in edges
                if edge.get("sourceId") in retained_ids and edge.get("targetId") in retained_ids
            ]
        visible_node_ids = set(node_ids)
        if view_mode == "context" and "requirement" in layers:
            visible_node_ids.update(
                str(node["id"])
                for node in cast(list[JsonObject], graph["nodes"])
                if node.get("layer") == "requirement"
            )
        if visible_node_ids == node_ids:
            dot_source = to_dot(projection, layers, node_ids, edge_ids, focus_id)
        else:
            dot_source = to_dot(projection, layers, visible_node_ids, edge_ids, focus_id)
        if visible_node_ids != node_ids:
            dot_source = "\n".join(
                line.replace(" changed", "")
                if 'class="node ' in line
                and not any(f'id="{node_id}"' in line for node_id in node_ids)
                else line
                for line in dot_source.splitlines()
            )
        dot_source = dot_source.replace('rankdir="LR"', f'rankdir="{direction}"', 1)
        svg = await self.runner.render(dot_source)
        return svg, str(revision["contentHash"])

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

    async def start_repository_baseline(self) -> str:
        agent_run_id, document = await asyncio.to_thread(self.store.begin_baseline_agent)
        revision = cast(JsonObject, document["revision"])
        context: JsonObject = {
            "agentRunId": agent_run_id,
            "task": "REPOSITORY_BASELINE",
            "baseRevisionId": revision["id"],
            "expectedFinalStatus": ["AWAITING_APPROVAL", "NEEDS_INPUT"],
        }
        prompt = await self._record_prompt(agent_run_id, "REPOSITORY_BASELINE", context)
        self._start(self._run_agent("REPOSITORY_BASELINE", agent_run_id, prompt))
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
            **(await self._revision_change_context(run_id, document)),
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
        finally:
            if task in {"IMPLEMENTATION", "SEMANTIC_REVIEW"} and implementation_run_id:
                try:
                    run = await asyncio.to_thread(
                        self.store.implementation_run, implementation_run_id
                    )
                except Exception:
                    logger.exception("读取终态开发运行失败，无法清理 worktree")
                else:
                    if run.get("status") in {
                        "COMPLETED",
                        "FAILED",
                        "BLOCKED",
                        "NEEDS_INPUT",
                    } and isinstance(run.get("worktree"), str):
                        await self._cleanup_worktree(
                            implementation_run_id, Path(cast(str, run["worktree"]))
                        )

    async def _cleanup_worktree(self, run_id: str, worktree: Path) -> None:
        """尽力回收受管 worktree，且不覆盖原运行结果。"""
        try:
            await self.runner.cleanup_worktree(run_id, worktree)
        except Exception:
            logger.exception("清理开发 worktree 失败：%s", run_id)

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
            **(await self._revision_change_context(run_id, document)),
        }
        prompt = await self._record_prompt(agent_run_id, "SEMANTIC_REVIEW", context)
        await self._run_agent(
            "SEMANTIC_REVIEW",
            agent_run_id,
            prompt,
            run_id,
            Path(worktree_value),
        )

    async def _revision_change_context(self, run_id: str, document: JsonObject) -> JsonObject:
        """生成实现与语义 Review 共用的确定性 revision 差异上下文。"""
        base, fixed_document = await asyncio.to_thread(self.store.run_revision_documents, run_id)
        return revision_change_context(base, fixed_document)

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
