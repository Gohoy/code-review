from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from mcp.server.fastmcp import FastMCP

from app.bootstrap import load_store
from app.config import ROOT, Settings
from app.graph import JsonObject, approval_errors, canonical_json, code_ownership, node_context
from app.indexer import index_repository
from app.prompt import PromptCatalog
from app.runner import VALIDATION_COMMANDS, Runner
from app.service import revision_change_context, validation_context_hash
from app.store import StoreError

settings = Settings.from_env()
store, _, _ = load_store(settings)
runner = Runner(settings)
prompts = PromptCatalog(settings.prompt_dir)
agent_run_id = os.getenv("SDBP_REVIEW_AGENT_RUN_ID", "")
implementation_run_id = os.getenv("SDBP_REVIEW_IMPLEMENTATION_RUN_ID") or None

mcp = FastMCP(
    "sdbp-review",
    instructions=(
        "sdbp-review 的统一图、代码索引和本地交付能力。"
        "先读 Resource，再调用完成当前目标所需的最少 Tool。"
    ),
)


@mcp.resource("project://current/state", mime_type="application/json")
def project_state() -> str:
    state = store.state()
    revision = cast(JsonObject, cast(JsonObject, state["revision"])["revision"])
    value: JsonObject = {
        "requirement": cast(JsonObject, state["requirement"]),
        "repository": cast(JsonObject, state["repository"]),
        "revision": revision,
        "agentRun": cast(JsonObject, state["agentRun"])
        if isinstance(state.get("agentRun"), dict)
        else None,
        "implementationRun": cast(JsonObject, state["implementationRun"])
        if isinstance(state.get("implementationRun"), dict)
        else None,
        "messages": cast(list[JsonObject], state["messages"]),
    }
    return canonical_json(value)


@mcp.resource("project://current/rules", mime_type="text/markdown")
def project_rules() -> str:
    paths = [ROOT / "AGENTS.md", settings.repository / "AGENTS.md"]
    texts = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if path.is_file() and resolved not in seen:
            texts.append(path.read_text(encoding="utf-8"))
            seen.add(resolved)
    return "\n\n".join(texts)


@mcp.resource("project://current/test-profiles", mime_type="application/json")
def test_profiles() -> str:
    return json.dumps(
        {
            "id": "local",
            "cwd": "development_start 返回的 worktree",
            "commands": [" ".join(command) for command in VALIDATION_COMMANDS],
        },
        ensure_ascii=False,
    )


@mcp.resource("graph://revision/current", mime_type="application/json")
def current_graph() -> str:
    return canonical_json(store.current_document())


@mcp.resource("graph://node/{node_id}", mime_type="application/json")
def graph_node(node_id: str) -> str:
    return canonical_json(node_context(store.current_document(), node_id))


@mcp.resource("change://{run_id}", mime_type="application/json")
async def change(run_id: str) -> str:
    current = store.implementation_run(run_id)
    worktree_value = current.get("worktree")
    diff: JsonObject = {"status": "worktree 尚未创建", "diff": ""}
    if isinstance(worktree_value, str):
        diff = await runner.change(Path(worktree_value))
    return canonical_json(
        {
            "run": current,
            "change": diff,
            "tools": store.tool_invocations(run_id),
        }
    )


@mcp.prompt(name="common")
def common_prompt() -> str:
    return (settings.prompt_dir / "common.md").read_text(encoding="utf-8")


@mcp.prompt(name="repository_baseline")
def repository_baseline_prompt() -> str:
    return prompts.text("REPOSITORY_BASELINE")


@mcp.prompt(name="requirement_change")
def requirement_change_prompt() -> str:
    return prompts.text("REQUIREMENT_CHANGE")


@mcp.prompt(name="implementation")
def implementation_prompt() -> str:
    return prompts.text("IMPLEMENTATION")


@mcp.prompt(name="semantic_review")
def semantic_review_prompt() -> str:
    return prompts.text("SEMANTIC_REVIEW")


@mcp.tool()
async def repository_index() -> JsonObject:
    """扫描固定 Git 快照中的 Python、JavaScript 与 TypeScript 函数及当前图映射覆盖率。"""

    async def operation() -> JsonObject:
        snapshot = await runner.repository_snapshot()
        paths = snapshot["paths"]
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise StoreError("Git 文件清单格式无效")
        document = store.current_document()
        return await asyncio.to_thread(
            index_repository,
            settings.repository,
            cast(list[str], paths),
            str(snapshot["commitSha"]),
            str(snapshot["treeHash"]),
            cast(JsonObject, document["graph"]),
        )

    return await _tool("repository_index", "扫描项目自有函数", operation)


@mcp.tool()
async def repository_sync() -> JsonObject:
    """确定性同步当前固定快照中的全部函数、可解析调用关系和已有覆盖率证据。"""

    async def operation() -> JsonObject:
        snapshot = await runner.repository_snapshot()
        paths = snapshot["paths"]
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise StoreError("Git 文件清单格式无效")
        document = store.current_document()
        index = await asyncio.to_thread(
            index_repository,
            settings.repository,
            cast(list[str], paths),
            str(snapshot["commitSha"]),
            str(snapshot["treeHash"]),
            cast(JsonObject, document["graph"]),
        )
        candidate = await asyncio.to_thread(store.sync_code_index, _agent_id(), index)
        revision = cast(JsonObject, candidate["revision"])
        return {
            "revision": revision,
            "coverage": index["coverage"],
            "ownership": code_ownership(candidate),
            "migrationGaps": index["migrationGaps"],
            "errors": index["errors"],
        }

    return await _tool("repository_sync", "同步全部代码事实", operation)


@mcp.tool()
async def graph_query(node_id: str) -> JsonObject:
    """读取一个统一图节点、一跳邻居与关系。"""

    async def operation() -> JsonObject:
        return node_context(store.current_document(), node_id)

    return await _tool("graph_query", node_id, operation)


@mcp.tool()
async def graph_create_candidate(
    base_revision_id: str,
    upsert_nodes: list[dict[str, object]],
    delete_node_ids: list[str],
    upsert_edges: list[dict[str, object]],
    delete_edge_ids: list[str],
) -> JsonObject:
    """校验结构化图差异并原子创建候选 revision。"""

    async def operation() -> JsonObject:
        diff: JsonObject = {
            "baseRevisionId": base_revision_id,
            "upsertNodes": cast(list[JsonObject], upsert_nodes),
            "deleteNodeIds": delete_node_ids,
            "upsertEdges": cast(list[JsonObject], upsert_edges),
            "deleteEdgeIds": delete_edge_ids,
        }
        document = await asyncio.to_thread(store.create_candidate, _agent_id(), diff)
        revision = cast(JsonObject, document["revision"])
        return {
            "revision": revision,
            "approvalErrors": approval_errors(document),
        }

    return await _tool("graph_create_candidate", base_revision_id, operation)


@mcp.tool()
async def revision_request_approval() -> JsonObject:
    """检查当前候选是否可批准；只请求用户批准，不替用户批准。"""

    async def operation() -> JsonObject:
        document = store.current_document()
        revision = cast(JsonObject, document["revision"])
        errors = approval_errors(document)
        return {
            "revisionId": revision["id"],
            "contentHash": revision["contentHash"],
            "ready": revision["status"] == "CANDIDATE"
            and bool(revision["approvable"])
            and not errors,
            "errors": errors,
            "next": "等待用户在页面明确批准" if not errors else "继续对话或修正候选图",
        }

    return await _tool("revision_request_approval", "检查当前候选", operation)


@mcp.tool()
async def development_start() -> JsonObject:
    """为已批准 revision 创建隔离 Git worktree。"""

    async def operation() -> JsonObject:
        run_id = _implementation_id()
        worktree = await runner.create_worktree(run_id)
        await asyncio.to_thread(store.start_run, run_id, worktree)
        return {"runId": run_id, "worktree": str(worktree)}

    return await _tool("development_start", "创建隔离 worktree", operation)


@mcp.tool()
async def change_submit(summary: str) -> JsonObject:
    """记录实现 Agent 对当前 worktree 变更的中文摘要。"""

    async def operation() -> JsonObject:
        run_id = _implementation_id()
        await asyncio.to_thread(store.submit_change, run_id, summary)
        return {"runId": run_id, "status": "RUNNING", "summary": summary}

    return await _tool("change_submit", summary, operation)


@mcp.tool()
async def test_run() -> JsonObject:
    """在当前开发 worktree 执行项目固定测试说明。"""

    async def operation() -> JsonObject:
        run_id = _implementation_id()
        worktree = await asyncio.to_thread(store.begin_test, run_id)
        try:
            base, document = store.run_revision_documents(run_id)
            context = revision_change_context(base, document)
            scenario_ids = frozenset(cast(list[str], context["changedScenarioIds"]))
            summary = await runner.verify(worktree, scenario_ids)
        except Exception as error:
            await asyncio.to_thread(store.finish_test, run_id, False, str(error))
            raise
        context_hash = validation_context_hash(sorted(scenario_ids))
        await asyncio.to_thread(store.finish_test, run_id, True, summary, context_hash)
        return {"runId": run_id, "status": "VERIFIED", "summary": summary}

    return await _tool("test_run", "执行项目固定测试", operation)


@mcp.tool()
async def review_submit(status: str, summary: str) -> JsonObject:
    """提交独立语义 Review 的 PASS 或 BLOCKED 结论。"""

    async def operation() -> JsonObject:
        run_id = _implementation_id()
        await asyncio.to_thread(store.submit_review, run_id, status, summary)
        return {"runId": run_id, "status": status, "summary": summary}

    return await _tool("review_submit", f"{status}：{summary}", operation)


@mcp.tool()
async def delivery_merge() -> JsonObject:
    """固定测试和语义 Review 通过后安全快进合并本地分支。"""

    async def operation() -> JsonObject:
        run_id = _implementation_id()
        base, document = await asyncio.to_thread(store.run_revision_documents, run_id)
        context = revision_change_context(base, document)
        context_hash = validation_context_hash(cast(list[str], context["changedScenarioIds"]))
        worktree, revision_id = await asyncio.to_thread(
            store.begin_merge_verified, run_id, context_hash
        )
        try:
            summary = await runner.merge(worktree, revision_id)
        except Exception:
            await asyncio.to_thread(store.fail_run, run_id, "本地合并失败")
            raise
        await asyncio.to_thread(store.complete_delivery, run_id, summary)
        return {"runId": run_id, "status": "COMPLETED", "summary": summary}

    return await _tool("delivery_merge", "安全合并本地分支", operation)


async def _tool(
    name: str,
    input_summary: str,
    operation: Callable[[], Awaitable[JsonObject]],
) -> JsonObject:
    expected_task = {
        "repository_index": {"REPOSITORY_BASELINE", "REQUIREMENT_CHANGE"},
        "repository_sync": {"REPOSITORY_BASELINE"},
        "graph_query": {"REPOSITORY_BASELINE", "REQUIREMENT_CHANGE", "SEMANTIC_REVIEW"},
        "graph_create_candidate": {"REPOSITORY_BASELINE", "REQUIREMENT_CHANGE"},
        "revision_request_approval": {"REPOSITORY_BASELINE", "REQUIREMENT_CHANGE"},
        "development_start": {"IMPLEMENTATION"},
        "change_submit": {"IMPLEMENTATION"},
        "test_run": {"IMPLEMENTATION"},
        "review_submit": {"SEMANTIC_REVIEW"},
        "delivery_merge": {"SEMANTIC_REVIEW"},
    }[name]
    task = store.agent(_agent_id()).get("task")
    if task not in expected_task:
        raise StoreError(f"{task} Agent 不能调用 {name}")
    tool_id = await asyncio.to_thread(
        store.begin_tool,
        _agent_id(),
        implementation_run_id,
        name,
        input_summary,
    )
    try:
        result = await operation()
    except Exception as error:
        await asyncio.to_thread(store.finish_tool, tool_id, "FAILED", str(error))
        raise
    await asyncio.to_thread(
        store.finish_tool,
        tool_id,
        "COMPLETED",
        canonical_json(result),
    )
    return result


def _agent_id() -> str:
    if not agent_run_id:
        raise StoreError("MCP Server 缺少 Agent 运行上下文")
    return agent_run_id


def _implementation_id() -> str:
    if not implementation_run_id:
        raise StoreError("当前 Agent 没有开发运行上下文")
    return implementation_run_id


if __name__ == "__main__":
    mcp.run(transport="stdio")
