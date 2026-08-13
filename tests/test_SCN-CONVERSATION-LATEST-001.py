from pathlib import Path


def test_SCN_CONVERSATION_LATEST_001_打开定位最新且允许回看() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    assert "listRef.current.scrollTop = listRef.current.scrollHeight" in source
    assert "setAwayFromLatest" in source
    assert "返回最新消息" in source
    assert 'behavior: "smooth"' in source
