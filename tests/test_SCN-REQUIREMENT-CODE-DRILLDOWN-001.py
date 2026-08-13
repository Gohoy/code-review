from pathlib import Path


def test_SCN_REQUIREMENT_CODE_DRILLDOWN_001_投影收敛到已有服务方法() -> None:
    source = Path("app/service.py").read_text(encoding="utf-8")
    assert "async def graph_svg(" in source
    assert 'if "implementation" in layers:' in source
    assert 'node.get("kind") == "Module"' in source
    assert 'node.get("kind") == "Repository"' in source
    assert "expanded_modules = module_ids & related" in source
    assert "to_dot(projection, layers, node_ids, edge_ids, focus_id)" in source
    assert not Path("app/projection.py").exists()
