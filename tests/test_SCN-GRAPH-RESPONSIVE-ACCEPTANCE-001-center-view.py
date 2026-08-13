from pathlib import Path


def test_SCN_GRAPH_RESPONSIVE_ACCEPTANCE_001_fitView使用centerView居中() -> None:
    source = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    fit_view = source.split("const fitView = useCallback(() => {", 1)[1].split("  }, []);", 1)[0]
    executable_fit_view = "\n".join(
        line for line in fit_view.splitlines() if not line.lstrip().startswith("//")
    )

    assert 'api.centerView(scale, 200, "easeOut")' in executable_fit_view
    assert "api.setTransform" not in executable_fit_view
