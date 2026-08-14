from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from app.runner import Runner, RunnerError, _constant_value, _pytest_node_ids


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("scenario_id", ["SCN-AUTO-DEVELOPMENT-GATE-001"], ids=lambda value: value)
def test_SCN_AUTO_DEVELOPMENT_GATE_001_门禁逐项报告受保护路径(
    tmp_path: Path, scenario_id: str
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "model").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "app" / "runner.py").write_text(
        'VALIDATION_COMMANDS = (("uv", "run", "pytest"),)\n', encoding="utf-8"
    )
    (tmp_path / "app" / "graph.py").write_text("基线 = True\n", encoding="utf-8")
    (tmp_path / "model" / "review-tool.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "tests" / "test_existing.py").write_text(
        "def test_existing(): pass\n", encoding="utf-8"
    )
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

    (tmp_path / "app" / "runner.py").write_text(
        'VALIDATION_COMMANDS = (("true",),)\n', encoding="utf-8"
    )
    (tmp_path / "app" / "graph.py").write_text("基线 = False\n", encoding="utf-8")
    (tmp_path / "model" / "review-tool.json").write_text('{"changed": true}\n', encoding="utf-8")
    (tmp_path / "tests" / "test_existing.py").write_text(
        "def test_changed(): pass\n", encoding="utf-8"
    )

    runner = object.__new__(Runner)
    with pytest.raises(RunnerError) as captured:
        asyncio.run(runner._assert_implementation_gate(tmp_path, frozenset({scenario_id})))

    message = str(captured.value)
    assert "app/graph.py：统一图语义校验器不可修改" in message
    assert "model/review-tool.json：批准模型或 JSON Schema 不可修改" in message
    assert "tests/test_existing.py：基线已有测试不可修改或删除" in message
    assert "app/runner.py：固定验证命令不可修改或削弱" in message


@pytest.mark.parametrize("scenario_id", ["SCN-AUTO-DEVELOPMENT-GATE-001"], ids=lambda value: value)
def test_SCN_AUTO_DEVELOPMENT_GATE_001_识别新增测试场景名称(scenario_id: str) -> None:
    output = (
        "tests/test_new.py::test_gate[SCN-AUTO-DEVELOPMENT-GATE-001]\n1 test collected in 0.01s\n"
    )
    assert _pytest_node_ids(output, "tests/test_new.py") == (
        f"tests/test_new.py::test_gate[{scenario_id}]",
    )
    assert _constant_value('VALIDATION_COMMANDS = (("uv",),)', "VALIDATION_COMMANDS")


@pytest.mark.parametrize("scenario_id", ["SCN-AUTO-DEVELOPMENT-GATE-001"], ids=lambda value: value)
def test_SCN_AUTO_DEVELOPMENT_GATE_001_允许修改非校验调用解析函数(
    tmp_path: Path, scenario_id: str
) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "runner.py").write_text(
        'VALIDATION_COMMANDS = (("uv", "run", "pytest"),)\n', encoding="utf-8"
    )
    (tmp_path / "app" / "graph.py").write_text(
        "def validate_document():\n    return True\n\ndef _resolve_call():\n    return '旧'\n",
        encoding="utf-8",
    )
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
    (tmp_path / "app" / "graph.py").write_text(
        "def validate_document():\n    return True\n\ndef _resolve_call():\n    return '新'\n",
        encoding="utf-8",
    )

    runner = object.__new__(Runner)
    assert asyncio.run(runner._assert_implementation_gate(tmp_path, frozenset({scenario_id}))) == ()
