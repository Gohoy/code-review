from pathlib import Path


def test_SCN_REVISION_APPROVAL_CACHE_CONSISTENCY_001_SCN_GRAPH_STATUS_TRANSFER_001() -> None:
    frontend = Path("prototype/src/App.jsx").read_text(encoding="utf-8")
    http = Path("app/http.py").read_text(encoding="utf-8")

    assert "document.revision.id !== revisionId" in frontend
    assert "document.revision.contentHash !== contentHash" in frontend
    assert (
        "`/api/revision/${encodeURIComponent(revisionId)}?contentHash="
        "${encodeURIComponent(contentHash)}`"
    ) in frontend
    assert "contentHash: revision.contentHash" in frontend
    assert '"Cache-Control": "private, max-age=31536000, immutable"' in http
    assert '"ETag": etag' in http
