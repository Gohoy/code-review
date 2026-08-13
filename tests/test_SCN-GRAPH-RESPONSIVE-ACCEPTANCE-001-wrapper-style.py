from pathlib import Path


def test_SCN_GRAPH_RESPONSIVE_ACCEPTANCE_001_视口使用行内裁切样式() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")

    assert 'wrapperClass="graph-viewport"' in source
    assert 'wrapperStyle={{ overflow: "clip" }}' in source
