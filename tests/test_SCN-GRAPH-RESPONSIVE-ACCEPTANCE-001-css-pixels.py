from pathlib import Path


def test_SCN_GRAPH_RESPONSIVE_ACCEPTANCE_001_使用主图CSS像素适配全部入口() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    styles = Path("prototype/src/styles.css").read_text(encoding="utf-8")

    executable_source = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("//")
    )
    assert 'querySelector(".graph-transform > div > svg")' in source
    assert "svgElement.clientWidth" in source
    assert "svgElement.clientHeight" in source
    assert "svgElement.getBBox()" not in executable_source
    assert "Math.max(container.clientWidth - padding * 2, 1)" in source
    assert "Math.max(container.clientHeight - padding * 2, 1)" in source
    assert "[svg, layer, selectedId, fitView]" in source
    assert "new ResizeObserver(fitView)" in source
    assert "<GraphControls fitView={fitView} />" in source
    assert "maxScale={GRAPH_SCALE_LIMITS.max}" in source
    assert "width: 1450px" not in styles
    assert "width: 1300px" not in styles
    assert ".graph-transform svg { width: auto" not in styles
