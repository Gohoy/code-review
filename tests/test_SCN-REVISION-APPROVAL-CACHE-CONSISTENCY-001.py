from __future__ import annotations

import asyncio
from pathlib import Path
from typing import cast

from app.graph import JsonObject
from app.service import ReviewService


class _状态仓库:
    def state(self) -> JsonObject:
        return {
            "revision": {
                "revision": {
                    "id": "REV-TEST-001",
                    "status": "APPROVED",
                    "approvable": False,
                    "contentHash": "fixed-content",
                },
                "graph": {"nodes": [], "edges": []},
            }
        }


def test_SCN_REVISION_APPROVAL_CACHE_CONSISTENCY_001_不可变文档排除可变审批元数据() -> None:
    service = object.__new__(ReviewService)
    service.store = cast(object, _状态仓库())

    document = asyncio.run(service.revision("REV-TEST-001"))
    revision = cast(JsonObject, document["revision"])

    assert revision["id"] == "REV-TEST-001"
    assert revision["contentHash"] == "fixed-content"
    assert "status" not in revision
    assert "approvable" not in revision


def test_SCN_REVISION_APPROVAL_CACHE_CONSISTENCY_001_轻量状态覆盖缓存文档审批状态() -> None:
    frontend = Path("prototype/src/App.jsx").read_text(encoding="utf-8")

    assert "revision: { ...document.revision, ...status.revision }" in frontend
    assert "document.revision.id !== revisionId" in frontend
