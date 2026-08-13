from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.runner import Runner, RunnerError


@pytest.mark.parametrize("scenario_id", ["SCN-AUTO-DEVELOPMENT-GATE-001"], ids=lambda value: value)
def test_SCN_AUTO_DEVELOPMENT_GATE_001_基线测试生成逐项证据(
    tmp_path: Path, scenario_id: str
) -> None:
    runner = object.__new__(Runner)

    async def run(arguments: list[str], **_: object) -> str:
        if "--collect-only" in arguments:
            return f"tests/test_existing.py::test_gate[{scenario_id}]\n1 test collected"
        return f"PASSED tests/test_existing.py::test_gate[{scenario_id}]"

    runner._run = run  # type: ignore[method-assign]
    evidence = asyncio.run(runner._scenario_test_evidence(tmp_path, frozenset({scenario_id})))

    assert evidence == (
        f"OBSERVED PASS：{scenario_id}：tests/test_existing.py::test_gate[{scenario_id}]",
    )


@pytest.mark.parametrize("scenario_id", ["SCN-AUTO-DEVELOPMENT-GATE-001"], ids=lambda value: value)
def test_SCN_AUTO_DEVELOPMENT_GATE_001_无匹配测试时拒绝(tmp_path: Path, scenario_id: str) -> None:
    runner = object.__new__(Runner)

    async def run(arguments: list[str], **_: object) -> str:
        assert "--collect-only" in arguments
        return "tests/test_other.py::test_other\n1 test collected"

    runner._run = run  # type: ignore[method-assign]
    with pytest.raises(RunnerError, match=f"{scenario_id}：未收集到"):
        asyncio.run(runner._scenario_test_evidence(tmp_path, frozenset({scenario_id})))
