from __future__ import annotations

from pathlib import Path

from app.graph import JsonObject
from app.indexer import index_repository


def test_SCN_CODE_AUTHORITY_001_扫描多语言全部函数(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text(
        """def outer():
    helper()
    return lambda value: value

class Service:
    def run(self):
        return outer()
""",
        encoding="utf-8",
    )
    (tmp_path / "App.jsx").write_text(
        """export function App() {
  const submit = () => request();
  return items.map((item) => submit(item));
}
""",
        encoding="utf-8",
    )
    graph: JsonObject = {"nodes": [], "edges": []}

    result = index_repository(
        tmp_path,
        ["service.py", "App.jsx"],
        "1" * 40,
        "2" * 40,
        graph,
    )

    functions = result["functions"]
    assert isinstance(functions, list)
    names = {item["qualifiedName"] for item in functions}
    assert {"outer", "outer.匿名函数@3:12", "Service.run", "App", "App.submit"} <= names
    assert len(functions) == 6
    coverage = result["coverage"]
    assert isinstance(coverage, dict)
    assert coverage == {
        "functionCount": 6,
        "graphFunctionCount": 0,
        "mappedFunctionCount": 0,
        "unmappedFunctionCount": 6,
        "status": "UNKNOWN",
        "measuredFunctionCount": 0,
        "coveredFunctionCount": 0,
        "uncoveredFunctionCount": 0,
        "artifact": None,
    }
    outer = next(item for item in functions if item["qualifiedName"] == "outer")
    assert outer["calls"] == ["helper"]
    submit = next(item for item in functions if item["qualifiedName"] == "App.submit")
    assert submit["calls"] == ["request"]
    assert len({item["id"] for item in functions}) == len(functions)


def test_SCN_FUNCTION_COVERAGE_001_导入真实函数覆盖率(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        "def covered():\n    return 1\n\ndef missed():\n    return 2\n",
        encoding="utf-8",
    )
    (tmp_path / "coverage.json").write_text(
        '{"files":{"service.py":{"executed_lines":[1,2]}}}', encoding="utf-8"
    )

    result = index_repository(tmp_path, ["service.py"], "1" * 40, "2" * 40, {"nodes": []})

    coverage = result["coverage"]
    assert coverage["status"] == "OBSERVED"
    assert coverage["coveredFunctionCount"] == 1
    assert coverage["uncoveredFunctionCount"] == 1
    functions = result["functions"]
    assert [item["coverage"]["status"] for item in functions] == ["COVERED", "UNCOVERED"]
