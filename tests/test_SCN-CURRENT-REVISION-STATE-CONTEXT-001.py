import copy
from pathlib import Path
from typing import cast

import pytest
from test_scenarios import FakeRunner, make_service, run

from app.graph import JsonObject
from app.service import revision_change_context


def test_SCN_CURRENT_REVISION_STATE_CONTEXT_001_旧运行不覆盖当前_revision_差异(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        service = make_service(tmp_path, FakeRunner())
        await service.initialize()
        stored_state = service.store.state()
        current = cast(JsonObject, stored_state["revision"])
        base = cast(JsonObject, stored_state["baseRevision"])
        expected = revision_change_context(base, current)
        stored_state["implementationRun"] = {
            "id": "RUN-OLD",
            "revisionId": "REV-OLDER",
            "status": "COMPLETED",
            "directScenarioIds": ["SCN-OLD"],
            "inheritedScenarioIds": ["SCN-INHERITED-OLD"],
            "deliveryScenarioIds": ["SCN-INHERITED-OLD", "SCN-OLD"],
        }
        monkeypatch.setattr(service.store, "state", lambda: copy.deepcopy(stored_state))
        monkeypatch.setattr(
            service.store,
            "run_revision_documents",
            lambda _run_id: (_ for _ in ()).throw(AssertionError("不应读取旧运行 revision")),
        )

        state = await service.state()

        assert state["changedNodeIds"] == expected["changedNodeIds"]
        assert state["changedEdgeIds"] == expected["changedEdgeIds"]
        assert state["changedScenarioIds"] == expected["changedScenarioIds"]
        assert state["implementationRun"]["revisionId"] == "REV-OLDER"
        assert state["implementationRun"]["deliveryScenarioIds"] == [
            "SCN-INHERITED-OLD",
            "SCN-OLD",
        ]

    run(scenario())
