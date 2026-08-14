from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import mcp_server
from app.runner import RunnerError
from app.service import validation_context_hash


class 测试Store:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def agent(self, agent_run_id: str) -> dict[str, str]:
        return {"task": "SEMANTIC_REVIEW"}

    def begin_tool(
        self,
        agent_run_id: str,
        implementation_run_id: str | None,
        name: str,
        input_summary: str,
    ) -> int:
        return 1

    def finish_tool(self, tool_id: int, status: str, output: str) -> None:
        pass

    def run_delivery_scope(self, run_id: str) -> dict[str, list[str]]:
        return {"deliveryScenarioIds": ["SCN-LOCAL-WEB-ASSET-REFRESH-001"]}

    def begin_merge_verified(self, run_id: str, context_hash: str) -> tuple[Path, str]:
        assert context_hash == validation_context_hash(["SCN-LOCAL-WEB-ASSET-REFRESH-001"])
        return Path("/tmp/隔离-worktree"), "REV-TEST"

    def complete_delivery(self, run_id: str, summary: str) -> None:
        self.calls.append("complete_delivery")

    def fail_run(self, run_id: str, error: str) -> None:
        self.calls.append("fail_run")
        assert error == "代码已合并但本地前端产物构建失败"


class 测试Runner:
    def __init__(self, calls: list[str], *, build_fails: bool = False) -> None:
        self.calls = calls
        self.build_fails = build_fails

    async def merge(self, worktree: Path, revision_id: str) -> str:
        self.calls.append("merge")
        return "已自动合并到 main（123456789abc）"

    async def refresh_local_web_assets(self, summary: str) -> None:
        self.calls.append("refresh_local_web_assets")
        if self.build_fails:
            raise RunnerError("npm 执行失败")


def test_SCN_LOCAL_WEB_ASSET_REFRESH_001_真实交付成功时先刷新再完成(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(mcp_server, "store", 测试Store(calls))
    monkeypatch.setattr(mcp_server, "runner", 测试Runner(calls))
    monkeypatch.setattr(mcp_server, "agent_run_id", "AGENT-TEST")
    monkeypatch.setattr(mcp_server, "implementation_run_id", "RUN-TEST")

    result = asyncio.run(mcp_server.delivery_merge())

    assert result["status"] == "COMPLETED"
    assert calls == ["merge", "refresh_local_web_assets", "complete_delivery"]


def test_SCN_LOCAL_WEB_ASSET_REFRESH_001_真实交付构建失败时阻止完成(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(mcp_server, "store", 测试Store(calls))
    monkeypatch.setattr(mcp_server, "runner", 测试Runner(calls, build_fails=True))
    monkeypatch.setattr(mcp_server, "agent_run_id", "AGENT-TEST")
    monkeypatch.setattr(mcp_server, "implementation_run_id", "RUN-TEST")

    with pytest.raises(RunnerError, match="npm 执行失败"):
        asyncio.run(mcp_server.delivery_merge())

    assert calls == ["merge", "refresh_local_web_assets", "fail_run"]
