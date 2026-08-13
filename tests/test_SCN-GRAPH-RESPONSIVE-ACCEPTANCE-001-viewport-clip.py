import re
from pathlib import Path


def test_SCN_GRAPH_RESPONSIVE_ACCEPTANCE_001_画布视口裁切但不滚动() -> None:
    styles = Path("prototype/src/styles.css").read_text(encoding="utf-8")
    viewport_rule = re.search(r"\.graph-viewport\s*\{(?P<body>[^}]*)\}", styles)

    assert viewport_rule is not None
    declarations = viewport_rule.group("body")
    assert re.search(r"(?:^|;)\s*overflow\s*:\s*clip\s*(?:;|$)", declarations)
    assert not re.search(
        r"(?:^|;)\s*overflow(?:-[xy])?\s*:\s*(?:auto|scroll|hidden)\s*(?:;|$)",
        declarations,
    )
