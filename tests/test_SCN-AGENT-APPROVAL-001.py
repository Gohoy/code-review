import copy
from pathlib import Path
from typing import cast

from app.config import ROOT
from app.graph import JsonObject, load_object
from app.store import Store


def test_SCN_AGENT_APPROVAL_001_Agent请求批准后不自行启动开发(tmp_path: Path) -> None:
    store = Store(tmp_path / "review.sqlite3", load_object(ROOT / "model/graph.schema.json"))
    store.initialize(load_object(ROOT / "model/review-tool.json"), ())
    agent_run_id, document, _ = store.begin_agent("生成候选后等待用户批准")
    graph = cast(JsonObject, document["graph"])
    changed = copy.deepcopy(cast(list[JsonObject], graph["nodes"])[0])
    changed["summary"] = f"{changed['summary']} 等待用户明确批准。"
    revision = cast(JsonObject, document["revision"])
    candidate = store.create_candidate(
        agent_run_id,
        {
            "baseRevisionId": revision["id"],
            "upsertNodes": [changed],
            "deleteNodeIds": [],
            "upsertEdges": [],
            "deleteEdgeIds": [],
        },
    )
    store.finish_agent(
        agent_run_id,
        {"status": "AWAITING_APPROVAL", "reply": "请明确批准", "focusNodeIds": []},
    )

    state = store.state()

    assert candidate["revision"]["status"] == "CANDIDATE"
    assert state["agentRun"]["status"] == "AWAITING_APPROVAL"
    assert state["implementationRun"] is None
