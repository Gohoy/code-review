from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.runner import Runner


@pytest.mark.parametrize(
    "shared_dependency",
    [True, False],
    ids=["共享依赖", "隔离安装"],
)
def test_SCN_AUTO_DEVELOPMENT_GATE_001_SCN_LOCAL_AUTO_DELIVERY_001_固定验证不破坏共享依赖(
    tmp_path: Path, shared_dependency: bool
) -> None:
    worktree = tmp_path / "worktree"
    dependency = worktree / "prototype" / "node_modules"
    dependency.parent.mkdir(parents=True)
    source_marker = tmp_path / "source" / "node_modules" / "marker"
    source_marker.parent.mkdir(parents=True)
    source_marker.write_text("保持不变", encoding="utf-8")
    if shared_dependency:
        dependency.symlink_to(source_marker.parent, target_is_directory=True)

    calls: list[list[str]] = []
    runner = object.__new__(Runner)

    async def verify_gate(_: Path, __: frozenset[str]) -> tuple[str, ...]:
        return ()

    async def run(arguments: list[str], **_: object) -> str:
        calls.append(arguments)
        return ""

    async def evidence(_: Path, __: frozenset[str]) -> tuple[str, ...]:
        return ()

    runner.verify_gate = verify_gate  # type: ignore[method-assign]
    runner._run = run  # type: ignore[method-assign]
    runner._scenario_test_evidence = evidence  # type: ignore[method-assign]

    asyncio.run(runner.verify(worktree, frozenset()))

    assert (["npm", "--prefix", "prototype", "ci"] in calls) is not shared_dependency
    assert source_marker.read_text(encoding="utf-8") == "保持不变"
