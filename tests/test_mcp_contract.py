from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Coroutine
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import ROOT
from app.graph import load_object
from app.store import Store


def test_SCN_AGENT_MCP_ORCHESTRATION_001_标准客户端发现能力(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = Store(tmp_path / "review.sqlite3", load_object(ROOT / "model/graph.schema.json"))
        seed = load_object(ROOT / "model/review-tool.json")
        base = load_object(ROOT / "model/revision/REV-REVIEW-TOOL-011.json")
        store.initialize(seed, (base,))
        agent_run_id, _, _ = store.begin_agent("检查 MCP 契约")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
            cwd=str(ROOT),
            env={
                **os.environ,
                "SDBP_REVIEW_DATA_DIR": str(tmp_path),
                "SDBP_REVIEW_REPOSITORY": str(ROOT),
                "SDBP_REVIEW_WORKTREE_ROOT": str(tmp_path / "worktree"),
                "SDBP_REVIEW_AGENT_RUN_ID": agent_run_id,
            },
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            tools = {item.name for item in (await session.list_tools()).tools}
            resources = {str(item.uri) for item in (await session.list_resources()).resources}
            templates = {
                item.uriTemplate
                for item in (await session.list_resource_templates()).resourceTemplates
            }
            prompts = {item.name for item in (await session.list_prompts()).prompts}
            result = await session.call_tool("revision_request_approval", {})

        assert tools == {
            "repository_index",
            "repository_sync",
            "graph_query",
            "graph_create_candidate",
            "revision_request_approval",
            "development_start",
            "change_submit",
            "test_run",
            "review_submit",
            "delivery_merge",
        }
        assert resources == {
            "project://current/state",
            "project://current/rules",
            "project://current/test-profiles",
            "graph://revision/current",
        }
        assert templates == {"graph://node/{node_id}", "change://{run_id}"}
        assert prompts == {
            "common",
            "repository_baseline",
            "requirement_change",
            "implementation",
            "semantic_review",
        }
        assert not result.isError
        state = store.state()
        invocations = state["toolInvocations"]
        assert isinstance(invocations, list)
        assert invocations[0]["name"] == "revision_request_approval"
        assert invocations[0]["status"] == "COMPLETED"

    _run(scenario())


def _run(coroutine: Coroutine[object, object, None]) -> None:
    asyncio.run(coroutine)
