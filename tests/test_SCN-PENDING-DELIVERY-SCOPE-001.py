from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path
from typing import cast

from app.config import ROOT
from app.graph import JsonObject, canonical_json, graph_hash, load_object
from app.service import validation_context_hash
from app.store import REQUIREMENT_ID, Store


def _历史仓库(tmp_path: Path) -> tuple[Store, JsonObject]:
    seed = load_object(ROOT / "model" / "review-tool.json")
    store = Store(tmp_path / "review.sqlite3", load_object(ROOT / "model" / "graph.schema.json"))
    store.initialize(seed)

    current = copy.deepcopy(seed)
    cast(JsonObject, current["revision"]).update(
        {"id": "REV-REVIEW-TOOL-082", "baseRevisionId": "REV-REVIEW-TOOL-081"}
    )

    parent = copy.deepcopy(current)
    parent_revision = cast(JsonObject, parent["revision"])
    parent_revision.update({"id": "REV-REVIEW-TOOL-081", "baseRevisionId": "REV-REVIEW-TOOL-080"})
    graph = cast(JsonObject, parent["graph"])
    graph["nodes"] = [
        node
        for node in cast(list[JsonObject], graph["nodes"])
        if node["id"]
        not in {"SCN-PENDING-DELIVERY-SCOPE-001", "DESIGN-RULE-PENDING-DELIVERY-SCOPE"}
    ]
    graph["edges"] = [
        edge
        for edge in cast(list[JsonObject], graph["edges"])
        if edge["id"]
        not in {"EDGE-FLOW-PENDING-DELIVERY-SCOPE-001", "EDGE-PENDING-DELIVERY-SCOPE-REALIZED-BY"}
    ]
    ancestor = copy.deepcopy(parent)
    cast(JsonObject, ancestor["revision"]).update(
        {"id": "REV-REVIEW-TOOL-080", "baseRevisionId": None}
    )
    current_nodes = cast(list[JsonObject], cast(JsonObject, current["graph"])["nodes"])
    scenario_template = copy.deepcopy(
        next(node for node in current_nodes if node["kind"] == "Scenario")
    )
    desktop = copy.deepcopy(scenario_template)
    desktop.update(
        {
            "id": "SCN-DESKTOP-STATUS-LAYOUT-001",
            "title": "桌面状态区不遮挡统一图",
            "summary": "测试用祖先待交付场景。",
        }
    )
    cast(list[JsonObject], cast(JsonObject, ancestor["graph"])["nodes"]).append(
        copy.deepcopy(desktop)
    )
    desktop["summary"] = "测试用后续修改的祖先待交付场景。"
    for document in (parent, current):
        cast(list[JsonObject], cast(JsonObject, document["graph"])["nodes"]).append(
            copy.deepcopy(desktop)
        )
    scenario_template.update(
        {
            "id": "SCN-PENDING-DELIVERY-SCOPE-001",
            "title": "后代交付继承尚未完成的场景范围",
            "summary": "测试用交付范围场景。",
        }
    )
    current_nodes.append(scenario_template)
    for document in (ancestor, parent, current):
        cast(JsonObject, document["revision"])["contentHash"] = graph_hash(
            cast(JsonObject, document["graph"])
        )

    with sqlite3.connect(store.path) as connection:
        for document in (ancestor, parent, current):
            revision = cast(JsonObject, document["revision"])
            connection.execute(
                """
                INSERT INTO revision (
                    id, requirement_id, repository_id, base_revision_id, status,
                    content_json, content_hash, approvable, created_at
                ) VALUES (?, ?, 'REPOSITORY-LOCAL', ?, 'APPROVED', ?, ?, 0, ?)
                """,
                (
                    revision["id"],
                    REQUIREMENT_ID,
                    revision.get("baseRevisionId"),
                    canonical_json(document),
                    revision["contentHash"],
                    "2026-08-14T00:00:00+00:00",
                ),
            )
        connection.execute("UPDATE repository SET current_revision_id = 'REV-REVIEW-TOOL-082'")
        connection.execute("UPDATE requirement SET current_revision_id = 'REV-REVIEW-TOOL-082'")
    return store, current


def test_SCN_PENDING_DELIVERY_SCOPE_001_阻断父级与空范围成功仍由后代继承(
    tmp_path: Path,
) -> None:
    store, _ = _历史仓库(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        now = "2026-08-14T00:10:00+00:00"
        connection.executemany(
            """
            INSERT INTO implementation_run (
                id, requirement_id, revision_id, status, direct_scenario_ids_json,
                inherited_scenario_ids_json, delivery_scenario_ids_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?)
            """,
            [
                (
                    "RUN-BLOCKED",
                    REQUIREMENT_ID,
                    "REV-REVIEW-TOOL-080",
                    "BLOCKED",
                    '["SCN-DESKTOP-STATUS-LAYOUT-001"]',
                    '["SCN-DESKTOP-STATUS-LAYOUT-001"]',
                    now,
                    now,
                ),
                (
                    "RUN-EMPTY-COMPLETED",
                    REQUIREMENT_ID,
                    "REV-REVIEW-TOOL-081",
                    "COMPLETED",
                    "[]",
                    "[]",
                    now,
                    now,
                ),
            ],
        )
        direct, inherited = store._delivery_scope(connection, "REV-REVIEW-TOOL-082")

    assert direct == ["SCN-PENDING-DELIVERY-SCOPE-001"]
    assert "SCN-DESKTOP-STATUS-LAYOUT-001" in inherited


def test_SCN_PENDING_DELIVERY_SCOPE_001_只有明确包含场景的成功运行才清除(
    tmp_path: Path,
) -> None:
    store, _ = _历史仓库(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        now = "2026-08-14T00:20:00+00:00"
        connection.execute(
            """
            INSERT INTO implementation_run (
                id, requirement_id, revision_id, status, direct_scenario_ids_json,
                inherited_scenario_ids_json, delivery_scenario_ids_json, created_at, updated_at
            ) VALUES ('RUN-DELIVERED', ?, 'REV-REVIEW-TOOL-081', 'COMPLETED', '[]',
                      '["SCN-DESKTOP-STATUS-LAYOUT-001"]',
                      '["SCN-DESKTOP-STATUS-LAYOUT-001"]', ?, ?)
            """,
            (REQUIREMENT_ID, now, now),
        )
        _, inherited = store._delivery_scope(connection, "REV-REVIEW-TOOL-082")

    assert "SCN-DESKTOP-STATUS-LAYOUT-001" not in inherited


def test_SCN_PENDING_DELIVERY_SCOPE_001_旧成功不能抵消后续阻断修改(
    tmp_path: Path,
) -> None:
    store, _ = _历史仓库(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        connection.executemany(
            """
            INSERT INTO implementation_run (
                id, requirement_id, revision_id, status, delivery_scenario_ids_json,
                delivery_scope_recorded, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '[]', 0, ?, ?)
            """,
            [
                (
                    "RUN-LEGACY-COMPLETED",
                    REQUIREMENT_ID,
                    "REV-REVIEW-TOOL-080",
                    "COMPLETED",
                    "2026-08-14T00:05:00+00:00",
                    "2026-08-14T00:05:00+00:00",
                ),
                (
                    "RUN-LATER-BLOCKED",
                    REQUIREMENT_ID,
                    "REV-REVIEW-TOOL-081",
                    "BLOCKED",
                    "2026-08-14T00:10:00+00:00",
                    "2026-08-14T00:10:00+00:00",
                ),
            ],
        )
        _, inherited = store._delivery_scope(connection, "REV-REVIEW-TOOL-082")

    assert "SCN-DESKTOP-STATUS-LAYOUT-001" in inherited


def test_SCN_PENDING_DELIVERY_SCOPE_001_运行范围不可变且测试Review页面共用同一范围(
    tmp_path: Path,
) -> None:
    store, current = _历史仓库(tmp_path)
    revision = cast(JsonObject, current["revision"])
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        direct, inherited = store._delivery_scope(connection, str(revision["id"]))
        delivery = sorted(set(direct) | set(inherited))
        now = "2026-08-14T00:25:00+00:00"
        connection.execute(
            """
            INSERT INTO implementation_run (
                id, requirement_id, revision_id, status, direct_scenario_ids_json,
                inherited_scenario_ids_json, delivery_scenario_ids_json, created_at, updated_at
            ) VALUES ('RUN-FIXED', ?, ?, 'PENDING', ?, ?, ?, ?, ?)
            """,
            (
                REQUIREMENT_ID,
                revision["id"],
                json.dumps(direct),
                json.dumps(inherited),
                json.dumps(delivery),
                now,
                now,
            ),
        )
    run_id = "RUN-FIXED"
    scope = store.run_delivery_scope(run_id)
    run = store.implementation_run(run_id)

    assert scope["directScenarioIds"] == ["SCN-PENDING-DELIVERY-SCOPE-001"]
    assert "SCN-DESKTOP-STATUS-LAYOUT-001" in cast(list[str], scope["inheritedScenarioIds"])
    assert run["deliveryScenarioIds"] == scope["deliveryScenarioIds"]
    assert validation_context_hash(cast(list[str], scope["deliveryScenarioIds"]))

    mcp_source = (ROOT / "app" / "mcp_server.py").read_text(encoding="utf-8")
    implementation_prompt = (ROOT / "prompt" / "implementation.md").read_text(encoding="utf-8")
    review_prompt = (ROOT / "prompt" / "semantic-review.md").read_text(encoding="utf-8")
    page = (ROOT / "prototype" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert mcp_source.count('scope["deliveryScenarioIds"]') >= 1
    assert 'store.run_delivery_scope(run_id)["deliveryScenarioIds"]' in mcp_source
    assert "deliveryScenarioIds" in implementation_prompt
    assert "deliveryScenarioIds" in review_prompt
    assert "directScenarioIds" in page and "inheritedScenarioIds" in page


def test_SCN_PENDING_DELIVERY_SCOPE_001_失败重试创建独立且一致的范围(
    tmp_path: Path,
) -> None:
    store, current = _历史仓库(tmp_path)
    revision = cast(JsonObject, current["revision"])
    with sqlite3.connect(store.path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "UPDATE revision SET status = 'APPROVED', approvable = 0 WHERE id = ?",
            (revision["id"],),
        )
        now = "2026-08-14T00:30:00+00:00"
        direct, inherited = store._delivery_scope(connection, str(revision["id"]))
        delivery = sorted(set(direct) | set(inherited))
        connection.execute(
            """
            INSERT INTO implementation_run (
                id, requirement_id, revision_id, status, worktree,
                direct_scenario_ids_json, inherited_scenario_ids_json,
                delivery_scenario_ids_json, created_at, updated_at
            ) VALUES ('RUN-OLD', ?, ?, 'FAILED', '/tmp/old', ?, ?, ?, ?, ?)
            """,
            (
                REQUIREMENT_ID,
                revision["id"],
                json.dumps(direct),
                json.dumps(inherited),
                json.dumps(delivery),
                now,
                now,
            ),
        )

    run_id, _, _ = store.retry_delivery(str(revision["id"]), str(revision["contentHash"]))
    assert run_id != "RUN-OLD"
    assert store.run_delivery_scope(run_id) == store.run_delivery_scope("RUN-OLD")
    assert store.implementation_run(run_id)["worktree"] is None
