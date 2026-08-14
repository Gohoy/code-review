from __future__ import annotations

from typing import cast

from app.config import Settings
from app.graph import JsonObject, graph_hash, load_object
from app.store import Store


def load_store(settings: Settings) -> tuple[Store, JsonObject, tuple[JsonObject, ...]]:
    own_repository = settings.repository == settings.model_path.parent.parent
    seed = load_object(settings.model_path) if own_repository else _external_repository_seed()
    revision = cast(JsonObject, seed["revision"])
    base_id = revision.get("baseRevisionId")
    base_path = settings.revision_dir / f"{base_id}.json"
    history = (load_object(base_path),) if isinstance(base_id, str) and base_path.is_file() else ()
    store = Store(
        settings.data_dir / "review.sqlite3",
        load_object(settings.graph_schema_path),
        external_repository=not own_repository,
    )
    return store, seed, history


def _external_repository_seed() -> JsonObject:
    graph: JsonObject = {
        "layers": [
            {"id": "requirement", "title": "需求", "description": "用户可观察行为"},
            {"id": "design", "title": "技术设计", "description": "系统实现边界"},
            {"id": "implementation", "title": "代码", "description": "仓库真实代码"},
            {"id": "verification", "title": "验证", "description": "运行证据"},
        ],
        "entryNodeIds": ["ACTOR-REPOSITORY-USER"],
        "codeSnapshots": [],
        "nodes": [
            {
                "id": "ACTOR-REPOSITORY-USER",
                "layer": "requirement",
                "kind": "Actor",
                "title": "目标仓库用户",
                "summary": "待由仓库证据具体化的用户入口。",
                "source": "INFERRED",
                "details": {"baselinePlaceholder": True, "version": "1.0.0"},
            },
            {
                "id": "QUESTION-REPOSITORY-BASELINE",
                "layer": "requirement",
                "kind": "Question",
                "title": "等待建立仓库基线",
                "summary": "请从目标仓库证据建立独立的用户行为与技术设计。",
                "source": "UNRESOLVED",
                "details": {"baselinePlaceholder": True, "version": "1.0.0"},
            },
        ],
        "edges": [
            {
                "id": "EDGE-REPOSITORY-BASELINE-BLOCKS",
                "sourceId": "QUESTION-REPOSITORY-BASELINE",
                "targetId": "ACTOR-REPOSITORY-USER",
                "kind": "blocks",
                "source": "DECLARED",
            }
        ],
    }
    return {
        "schemaVersion": 2,
        "modelId": "MODEL-REPOSITORY-BASELINE",
        "title": "待建立仓库行为基线",
        "revision": {
            "id": "REV-REPOSITORY-BASELINE-001",
            "status": "CANDIDATE",
            "baseRevisionId": None,
            "contentHash": graph_hash(graph),
            "approvable": False,
        },
        "graph": graph,
    }
