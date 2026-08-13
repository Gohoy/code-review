from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_SCN_CHANGED_SCENARIO_COVERAGE_001_实现与审阅提示完整覆盖变化场景() -> None:
    implementation = (ROOT / "prompt" / "implementation.md").read_text(encoding="utf-8")
    semantic_review = (ROOT / "prompt" / "semantic-review.md").read_text(encoding="utf-8")

    for prompt in (implementation, semantic_review):
        assert "changedNodeIds" in prompt
        assert "全部变化 Scenario" in prompt
        assert "实现锚点" in prompt
        assert "OBSERVED" in prompt

    assert "不得遗漏、合并或只选择其中一个场景" in implementation
    assert "不能用某一场景的证据替代其他场景" in semantic_review
    assert "必须提交 `BLOCKED`" in semantic_review
