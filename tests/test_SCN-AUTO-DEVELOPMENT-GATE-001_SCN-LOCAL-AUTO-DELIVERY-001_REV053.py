from __future__ import annotations

import asyncio
import sqlite3
import subprocess
from pathlib import Path
from typing import cast

import pytest

from app.runner import Runner, RunnerError
from app.service import ReviewService
from app.store import Store


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.parametrize("run_id", ["", ".", "..", "../RUN", "child/RUN", "/tmp/RUN"])
def test_SCN_AUTO_DEVELOPMENT_GATE_001_SCN_LOCAL_AUTO_DELIVERY_001_拒绝非法路径并安全修剪(
    tmp_path: Path, run_id: str
) -> None:
    runner = object.__new__(Runner)
    root = tmp_path / "worktrees"
    runner.settings = type("测试配置", (), {"worktree_root": root, "repository": tmp_path})()
    with pytest.raises(RunnerError, match="单个目录名"):
        asyncio.run(runner.cleanup_worktree(run_id, root / "RUN"))

    calls: list[list[str]] = []

    async def run(arguments: list[str], **_: object) -> str:
        calls.append(arguments)
        return ""

    runner._run = run  # type: ignore[method-assign]
    asyncio.run(runner.cleanup_worktree("RUN-MISSING", root / "RUN-MISSING"))
    assert calls == [["git", "-C", str(tmp_path), "worktree", "prune"]]


def test_SCN_AUTO_DEVELOPMENT_GATE_001_SCN_LOCAL_AUTO_DELIVERY_001_依赖链接不进入Git状态(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    (repository / ".gitignore").write_text(".venv/\nprototype/node_modules/\n", encoding="utf-8")
    (repository / "功能.txt").write_text("基线\n", encoding="utf-8")
    (repository / ".venv").mkdir()
    (repository / "prototype" / "node_modules").mkdir(parents=True)
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-m",
        "baseline",
    )
    runner = object.__new__(Runner)
    runner.settings = type(
        "测试配置", (), {"repository": repository, "worktree_root": tmp_path / "worktrees"}
    )()

    worktree = asyncio.run(runner.create_worktree("RUN-LINKS"))

    assert (worktree / ".venv").is_symlink()
    assert (worktree / "prototype" / "node_modules").is_symlink()
    assert _git(worktree, "status", "--porcelain") == ""
    asyncio.run(runner.cleanup_worktree("RUN-LINKS", worktree))


def test_SCN_AUTO_DEVELOPMENT_GATE_001_SCN_LOCAL_AUTO_DELIVERY_001_启动只清理既有终态(
    tmp_path: Path,
) -> None:
    database = tmp_path / "review.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE implementation_run (id TEXT, status TEXT, worktree TEXT, created_at TEXT)"
        )
        connection.executemany(
            "INSERT INTO implementation_run VALUES (?, ?, ?, ?)",
            [
                ("RUN-DONE", "COMPLETED", "/managed/RUN-DONE", "1"),
                ("RUN-FAILED", "FAILED", "/managed/RUN-FAILED", "2"),
                ("RUN-ACTIVE", "RUNNING", "/managed/RUN-ACTIVE", "3"),
            ],
        )
    assert Store(database, {}).terminal_worktrees() == (
        ("RUN-DONE", Path("/managed/RUN-DONE")),
        ("RUN-FAILED", Path("/managed/RUN-FAILED")),
    )


def test_SCN_AUTO_DEVELOPMENT_GATE_001_SCN_LOCAL_AUTO_DELIVERY_001_初始化顺序与失败保持(
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class 测试Store:
        def terminal_worktrees(self) -> tuple[tuple[str, Path], ...]:
            events.append("查询终态")
            return (("RUN-DONE", tmp_path / "RUN-DONE"),)

        def initialize(self, seed: object, history: object) -> None:
            del seed, history
            events.append("初始化")

    class 测试Runner:
        async def cleanup_worktree(self, run_id: str, worktree: Path) -> None:
            events.append(f"清理:{run_id}:{worktree.name}")
            raise RunnerError("模拟清理失败")

        async def dependency_status(self) -> dict[str, str]:
            events.append("依赖")
            return {}

    service = ReviewService(cast(Store, 测试Store()), cast(Runner, 测试Runner()), {}, ())
    asyncio.run(service.initialize())
    assert events == ["查询终态", "初始化", "清理:RUN-DONE:RUN-DONE", "依赖"]


def test_SCN_AUTO_DEVELOPMENT_GATE_001_SCN_LOCAL_AUTO_DELIVERY_001_场景测试例外且无关测试拒绝(
    tmp_path: Path,
) -> None:
    (tmp_path / "tests").mkdir()
    scenario_id = "SCN-AUTO-DEVELOPMENT-GATE-001"
    scenario_test = tmp_path / "tests" / f"test_{scenario_id}.py"
    unrelated_test = tmp_path / "tests" / "test_unrelated.py"
    scenario_test.write_text("def test_old(): pass\n", encoding="utf-8")
    unrelated_test.write_text("def test_old(): pass\n", encoding="utf-8")
    _git(tmp_path, "init")
    _git(tmp_path, "add", ".")
    _git(
        tmp_path,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@localhost",
        "commit",
        "-m",
        "baseline",
    )
    scenario_test.write_text("def test_new(): pass\n", encoding="utf-8")
    runner = object.__new__(Runner)
    assert asyncio.run(runner._assert_implementation_gate(tmp_path, frozenset({scenario_id}))) == ()

    unrelated_test.write_text("def test_changed(): pass\n", encoding="utf-8")
    with pytest.raises(RunnerError, match="test_unrelated.py：基线已有测试不可修改或删除"):
        asyncio.run(runner._assert_implementation_gate(tmp_path, frozenset({scenario_id})))
