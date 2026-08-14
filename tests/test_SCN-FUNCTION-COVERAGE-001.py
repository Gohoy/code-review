from pathlib import Path

from app.indexer import index_repository


def test_SCN_FUNCTION_COVERAGE_001_导入_lcov_并在缺少产物时返回未知(
    tmp_path: Path,
) -> None:
    source = tmp_path / "service.py"
    source.write_text("def covered():\n    return 1\n", encoding="utf-8")

    unknown = index_repository(tmp_path, ["service.py"], "1" * 40, "2" * 40, {"nodes": []})
    assert unknown["coverage"]["status"] == "UNKNOWN"
    assert unknown["functions"][0]["coverage"] == {
        "status": "UNKNOWN",
        "artifact": None,
        "coveredLineCount": None,
    }

    (tmp_path / "lcov.info").write_text(
        f"SF:{source}\nDA:1,1\nDA:2,1\nend_of_record\n", encoding="utf-8"
    )
    observed = index_repository(tmp_path, ["service.py"], "1" * 40, "2" * 40, {"nodes": []})
    function = observed["functions"][0]
    assert observed["coverage"]["artifact"] == "lcov.info"
    assert function["coverage"]["status"] == "COVERED"
    assert function["snapshotId"] == observed["snapshot"]["id"]
