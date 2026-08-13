from pathlib import Path


def test_SCN_GRAPH_STATUS_TRANSFER_001_轻量状态与显式版本缓存() -> None:
    http = Path("app/http.py").read_text(encoding="utf-8")
    service = Path("app/service.py").read_text(encoding="utf-8")
    assert "await service.status()" in http
    assert 'request.query_params.get("revisionId")' in http
    assert 'request.query_params.get("layer")' in http
    assert '"ETag"' in http and "immutable" in http
    assert 'state.pop("revision")' in service
    assert 'state.pop("baseRevision", None)' in service
