from pathlib import Path


def test_SCN_GRAPH_RESPONSIVE_ACCEPTANCE_001_仅适配主图而非工具栏图标() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    assert 'querySelector(".graph-transform > div > svg")' in source
    assert 'container?.querySelector("svg")' not in source
