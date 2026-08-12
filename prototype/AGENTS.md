# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

## 已选视觉方向

- 以用户选中的“变更叙事审阅台”图片为唯一视觉基准。
- 页面保持左侧需求对话、中间统一图变更、右侧节点检查器和底部批准栏同时可见。
- 默认突出当前 revision 的变化，未变化上下文弱化；需求节点使用蓝色，技术设计节点使用紫色，新增与修改使用克制的绿色。
- 核心操作只有继续讨论、切换变化与上下文、选择节点和“批准并开始开发”。

## 实现取舍

- 产品图用于确认信息架构和核心流程，不要求逐像素还原 CSS。
- 前端统一使用 Ant Design 封装组件和响应式能力，只保留布局、行为图 SVG 与必要断点的少量 CSS。
- 桌面端同时展示对话、图和详情；窄屏将对话和详情收进 Drawer，主画布保持完整可操作。

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.
