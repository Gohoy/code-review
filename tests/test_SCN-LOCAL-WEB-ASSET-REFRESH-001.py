from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.config import ROOT, Settings
from app.runner import Runner, RunnerError


def _runner(repository: Path, calls: list[tuple[list[str], Path]]) -> Runner:
    runner = cast(Runner, object.__new__(Runner))
    runner.settings = cast(Settings, SimpleNamespace(repository=repository))

    async def run(
        arguments: list[str],
        *,
        cwd: Path,
        input_text: str | None = None,
        timeout: int,
        extra_environment: dict[str, str] | None = None,
    ) -> str:
        calls.append((arguments, cwd))
        return ""

    runner._run = run  # type: ignore[method-assign]
    return runner


def test_SCN_LOCAL_WEB_ASSET_REFRESH_001_自仓代码变化时构建一次() -> None:
    calls: list[tuple[list[str], Path]] = []
    runner = _runner(ROOT, calls)

    asyncio.run(runner.refresh_local_web_assets("已自动合并到 main（123456789abc）"))

    assert calls == [(["npm", "--prefix", "prototype", "run", "build"], ROOT)]


@pytest.mark.parametrize(
    ("repository", "summary"),
    [
        (Path("/tmp/外部仓库"), "已自动合并到 main（123456789abc）"),
        (ROOT, "代码无变化，无需合并"),
    ],
)
def test_SCN_LOCAL_WEB_ASSET_REFRESH_001_外部仓库或无变化时跳过(
    repository: Path, summary: str
) -> None:
    calls: list[tuple[list[str], Path]] = []
    runner = _runner(repository, calls)

    asyncio.run(runner.refresh_local_web_assets(summary))

    assert calls == []


def test_SCN_LOCAL_WEB_ASSET_REFRESH_001_构建失败向上抛出() -> None:
    runner = _runner(ROOT, [])

    async def fail(
        arguments: list[str],
        *,
        cwd: Path,
        input_text: str | None = None,
        timeout: int,
        extra_environment: dict[str, str] | None = None,
    ) -> str:
        raise RunnerError("npm 执行失败")

    runner._run = fail  # type: ignore[method-assign]

    with pytest.raises(RunnerError, match="npm 执行失败"):
        asyncio.run(runner.refresh_local_web_assets("已自动合并到 main（123456789abc）"))
