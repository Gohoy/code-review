from pathlib import Path
from typing import cast

from app.config import ROOT
from app.graph import JsonObject, load_object
from app.store import Store


def test_SCN_AGENT_TOOL_ACTIVITY_001_持久化运行成功失败与摘要(tmp_path: Path) -> None:
    store = Store(tmp_path / "review.sqlite3", load_object(ROOT / "model/graph.schema.json"))
    store.initialize(load_object(ROOT / "model/review-tool.json"), ())
    agent_run_id, _, _ = store.begin_agent("记录工具活动")

    completed_id = store.begin_tool(agent_run_id, None, "graph_query", "输入摘要")
    running = cast(list[JsonObject], store.state()["toolInvocations"])
    assert running[0]["status"] == "RUNNING"
    assert running[0]["inputSummary"] == "输入摘要"
    store.finish_tool(completed_id, "COMPLETED", "输出摘要")

    failed_id = store.begin_tool(agent_run_id, None, "test_run", "失败输入")
    store.finish_tool(failed_id, "FAILED", "真实失败摘要")
    activities = cast(list[JsonObject], store.state()["toolInvocations"])
    by_id = {str(item["id"]): item for item in activities}
    assert by_id[completed_id]["status"] == "COMPLETED"
    assert by_id[completed_id]["outputSummary"] == "输出摘要"
    assert by_id[failed_id]["status"] == "FAILED"
    assert by_id[failed_id]["outputSummary"] == "真实失败摘要"
