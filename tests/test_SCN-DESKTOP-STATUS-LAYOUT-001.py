from pathlib import Path


def test_SCN_DESKTOP_STATUS_LAYOUT_001_状态页脚不再顶出统一图() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    styles = Path("prototype/src/styles.css").read_text(encoding="utf-8")

    assert ".graph-svg { position: relative; width: 100%; height: 100%; min-height: 0;" in styles
    assert ".approval-bar { max-height: 34dvh;" in styles
    assert "overflow: auto" in styles
    assert 'className="review-shell"' in source
    assert 'className="graph-content"' in source
    assert 'className="approval-bar"' in source


def test_SCN_DESKTOP_STATUS_LAYOUT_001_长摘要两行折叠且工具活动默认收起() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    styles = Path("prototype/src/styles.css").read_text(encoding="utf-8")

    assert "const [deliverySummaryOpen, setDeliverySummaryOpen] = useState(false)" in source
    assert 'deliverySummaryOpen ? "" : "delivery-summary-clamped"' in source
    assert '{deliverySummaryOpen ? "收起" : "展开"}' in source
    assert "-webkit-line-clamp: 2" in styles
    agent_activity = source[
        source.index("function AgentActivity") : source.index("export function ReviewApp")
    ]
    assert "<Collapse" in agent_activity
    assert "defaultActiveKey" not in agent_activity


def test_SCN_DESKTOP_STATUS_LAYOUT_001_移动端状态与节点详情保持按需展开() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    styles = Path("prototype/src/styles.css").read_text(encoding="utf-8")

    assert "const [footerOpen, setFooterOpen] = useState(false)" in source
    assert '{footerOpen ? "收起状态详情"' in source
    assert 'title="节点详情"' in source
    assert ".mobile-summary-hidden { display: none; }" in styles
    assert ".approval-bar { max-height: 42dvh;" in styles
