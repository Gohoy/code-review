import assert from "node:assert/strict";
import { access } from "node:fs/promises";
import test from "node:test";
import { retainTestEvidence } from "../src/graph.js";
import worker from "../worker/index.js";

test("SCN-RUN-TEST-EVIDENCE-001：同一运行刷新时保留已展示的测试证据", () => {
  const projection = { implementationRunId: "RUN-1", nodes: [{ id: "EVIDENCE-1" }], edges: [] };

  assert.equal(retainTestEvidence(projection, "RUN-1"), projection);
  assert.deepEqual(retainTestEvidence(projection, "RUN-2"), {
    implementationRunId: "RUN-2",
    nodes: [],
    edges: [],
  });
});

test("SCN-GRAPH-EXPLORE-001：直接返回已有静态资源", async () => {
  const calls = [];
  const response = await worker.fetch(new Request("https://example.test/assets/app.js"), {
    ASSETS: {
      fetch: async (request) => {
        calls.push(new URL(request.url).pathname);
        return new Response("asset", { status: 200 });
      },
    },
  });

  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["/assets/app.js"]);
});

test("SCN-GRAPH-EXPLORE-001：未知页面路径返回应用入口", async () => {
  const calls = [];
  const response = await worker.fetch(
    new Request("https://example.test/flow/step-two?source=share", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async (request) => {
          const url = new URL(request.url);
          calls.push(url.pathname + url.search);
          return new Response(url.pathname === "/index.html" ? "app" : "missing", {
            status: url.pathname === "/index.html" ? 200 : 404,
          });
        },
      },
    },
  );

  assert.equal(response.status, 200);
  assert.deepEqual(calls, ["/flow/step-two?source=share", "/index.html"]);
});

test("SCN-REQ-DIALOG-001：接口与写请求不回退到应用入口", async () => {
  for (const request of [
    new Request("https://example.test/api/missing", { headers: { accept: "application/json" } }),
    new Request("https://example.test/flow", { method: "POST", headers: { accept: "text/html" } }),
  ]) {
    let calls = 0;
    const response = await worker.fetch(request, {
      ASSETS: {
        fetch: async () => {
          calls += 1;
          return new Response("missing", { status: 404 });
        },
      },
    });

    assert.equal(response.status, 404);
    assert.equal(calls, 1);
  }
});

test("SCN-GRAPH-EXPLORE-001：构建包含 Sites 交付文件", async () => {
  await access(new URL("../dist/client/index.html", import.meta.url));
  await access(new URL("../dist/server/index.js", import.meta.url));
  await access(new URL("../dist/.openai/hosting.json", import.meta.url));
});
