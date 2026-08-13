from pathlib import Path


def test_SCN_AGENT_MCP_ORCHESTRATION_001_Codex配置名与资源读取名一致() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "runner.py").read_text(encoding="utf-8")

    assert "mcp_servers.sdbp-review.command" in source
    assert "mcp_servers.sdbp_review.command" not in source
