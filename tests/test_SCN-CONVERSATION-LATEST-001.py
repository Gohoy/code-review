import sqlite3
from pathlib import Path

from app.config import ROOT
from app.graph import load_object
from app.store import REQUIREMENT_ID, Store


def test_SCN_CONVERSATION_LATEST_001_打开定位最新且允许回看() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    assert "listRef.current.scrollTop = listRef.current.scrollHeight" in source
    assert "setAwayFromLatest" in source
    assert "返回最新消息" in source
    assert 'behavior: "smooth"' in source


def test_SCN_CONVERSATION_LATEST_001_状态和Agent始终读取最近两百条消息(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "review.sqlite3", load_object(ROOT / "model/graph.schema.json"))
    store.initialize(load_object(ROOT / "model/review-tool.json"))
    with sqlite3.connect(store.path) as connection:
        connection.execute("DELETE FROM message")
        connection.executemany(
            """
            INSERT INTO message (requirement_id, role, content, created_at)
            VALUES (?, 'USER', ?, ?)
            """,
            (
                (REQUIREMENT_ID, f"消息-{index:03d}", f"2026-08-14T00:{index:03d}:00+00:00")
                for index in range(205)
            ),
        )

    visible = store.state()["messages"]
    _, _, conversation = store.begin_agent("最新需求")

    assert len(visible) == 200
    assert visible[0]["content"] == "消息-005"
    assert visible[-1]["content"] == "消息-204"
    assert len(conversation) == 200
    assert conversation[0]["content"] == "消息-006"
    assert conversation[-1]["content"] == "最新需求"
