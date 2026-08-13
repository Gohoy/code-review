from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import cast

from starlette.testclient import TestClient

from app.config import ROOT
from app.graph import JsonObject, load_object
from app.http import create_app
from app.runner import Runner
from app.service import ReviewService
from app.store import Store


class 捕获投影运行器:
    def __init__(self) -> None:
        self.dots: list[str] = []

    async def dependency_status(self) -> JsonObject:
        return {}

    async def render(self, dot_source: str) -> str:
        self.dots.append(dot_source)
        return f"<svg><title>{len(self.dots)}</title></svg>"


def _真实应用(tmp_path: Path) -> tuple[object, 捕获投影运行器, str]:
    document = copy.deepcopy(load_object(ROOT / "model" / "review-tool.json"))
    revision = cast(JsonObject, document["revision"])
    runner = 捕获投影运行器()
    store = Store(tmp_path / "review.sqlite3", load_object(ROOT / "model" / "graph.schema.json"))
    service = ReviewService(store, cast(Runner, runner), document)
    return create_app(service, tmp_path), runner, str(revision["id"])


def test_SCN_GRAPH_RESPONSIVE_ACCEPTANCE_001_真实投影与缓存区分模式方向(
    tmp_path: Path,
) -> None:
    app, runner, revision_id = _真实应用(tmp_path)
    with TestClient(cast(object, app)) as client:
        base = f"/api/graph.svg?revisionId={revision_id}&layer=requirement&focusId="
        lr_url = f"{base}&viewMode=changes&direction=LR"
        tb_url = f"{base}&viewMode=changes&direction=TB"
        context_url = f"{base}&viewMode=context&direction=LR"
        lr = client.get(lr_url)
        tb = client.get(tb_url)
        context = client.get(context_url)
        assert lr.status_code == tb.status_code == context.status_code == 200
        assert 'rankdir="LR"' in runner.dots[0]
        assert 'rankdir="TB"' in runner.dots[1]
        assert len({lr.headers["etag"], tb.headers["etag"], context.headers["etag"]}) == 3
        assert client.get(lr_url, headers={"If-None-Match": lr.headers["etag"]}).status_code == 304
        assert client.get(tb_url, headers={"If-None-Match": lr.headers["etag"]}).status_code == 200


def test_SCN_GRAPH_RESPONSIVE_ACCEPTANCE_001_模式方向变化重新触发画布适配(
    tmp_path: Path,
) -> None:
    script = ROOT / "prototype" / "graph-fit.test.mjs"
    script.write_text(
        """
import assert from "node:assert/strict";
import { createServer } from "vite";

const server = await createServer({
  root: process.cwd(),
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "silent",
});
try {
  const { fitGraphView, graphProjectionKey, observeGraphFit } =
    await server.ssrLoadModule("/src/App.jsx");
  let observedCount = 0;
  let disconnectedCount = 0;
  const centerCalls = [];
  const container = {
    clientWidth: 800,
    clientHeight: 600,
    querySelector: () => ({ clientWidth: 1000, clientHeight: 500 }),
  };
  globalThis.requestAnimationFrame = (callback) => { callback(); return 1; };
  globalThis.cancelAnimationFrame = () => {};
  globalThis.ResizeObserver = class {
    constructor(callback) { this.callback = callback; }
    observe() { observedCount += 1; this.callback(); }
    disconnect() { disconnectedCount += 1; }
  };
  const states = [["changes", "LR"], ["context", "LR"], ["context", "TB"]];
  const keys = states.map((state) => graphProjectionKey(...state));
  assert.equal(new Set(keys).size, 3);
  for (const state of states) {
    graphProjectionKey(...state);
    const cleanup = observeGraphFit(() => {
      const scale = fitGraphView(container);
      centerCalls.push([scale, 200, "easeOut"]);
    }, {});
    cleanup();
  }
  assert.equal(centerCalls.length, 6);
  assert.deepEqual(centerCalls[0], [0.736, 200, "easeOut"]);
  assert.equal(observedCount, 3);
  assert.equal(disconnectedCount, 3);
} finally {
  await server.close();
}
""".strip(),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["node", str(script)],
            cwd=ROOT / "prototype",
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
    finally:
        script.unlink(missing_ok=True)
