from pathlib import Path


def test_SCN_GRAPH_RESPONSIVE_ACCEPTANCE_001_缩放与方向使用统一边界() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    assert "GRAPH_SCALE_LIMITS = { min: 0.75, max: 1 }" in source
    assert "minScale={GRAPH_SCALE_LIMITS.min}" in source
    assert "maxScale={GRAPH_SCALE_LIMITS.max}" in source
    assert 'aria-label="统一图布局方向"' in source
    assert '{ label: "横向", value: "LR" }' in source
    assert '{ label: "纵向", value: "TB" }' in source
