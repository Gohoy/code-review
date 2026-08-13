from pathlib import Path


def test_SCN_GRAPH_RESPONSIVE_ACCEPTANCE_001_按容器与图边界动态适配() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    assert "svgElement.clientWidth || viewBox?.width" in source
    assert "container.clientWidth" in source
    assert "container.clientHeight" in source
    assert "new ResizeObserver(fitView)" in source
    assert "<GraphControls fitView={fitView} />" in source
