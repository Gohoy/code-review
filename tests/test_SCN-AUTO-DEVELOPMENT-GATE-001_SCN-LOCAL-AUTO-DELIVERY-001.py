from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.runner import Runner, RunnerError


def test_SCN_AUTO_DEVELOPMENT_GATE_001_SCN_LOCAL_AUTO_DELIVERY_001_拒绝名称不符的受管路径(
    tmp_path: Path,
) -> None:
    run_id = "RUN-EXPECTED"
    root = tmp_path / "worktrees"
    mismatched = root / "RUN-OTHER"
    mismatched.mkdir(parents=True)
    runner = object.__new__(Runner)
    runner.settings = type(
        "测试配置",
        (),
        {"worktree_root": root, "repository": tmp_path},
    )()

    with pytest.raises(RunnerError, match="不匹配受管路径"):
        asyncio.run(runner.cleanup_worktree(run_id, mismatched))
