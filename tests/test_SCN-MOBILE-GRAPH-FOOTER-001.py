from pathlib import Path


def test_SCN_MOBILE_GRAPH_FOOTER_001_窄屏状态详情默认收起() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    styles = Path("prototype/src/styles.css").read_text(encoding="utf-8")
    assert "const [footerOpen, setFooterOpen] = useState(false)" in source
    assert "收起状态详情" in source and "状态详情" in source
    assert "mobile-summary-hidden" in source
    assert ".mobile-summary-hidden { display: none; }" in styles
    assert "max-height: 42dvh" in styles
