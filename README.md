# sdbp-review

SDBP 的统一图模型驱动需求、技术设计、开发、测试与代码 Review 工具。

第一里程碑已经实现：用户通过自然语言让本机 Codex 生成需求与技术设计图差异，明确批准后，系统才会在隔离 Git worktree 中启动开发。唯一数据源是
[统一图模型](model/review-tool.json)。当前批准版本为 `REV-REVIEW-TOOL-009`；图与代码权威契约候选版本为 `REV-REVIEW-TOOL-010`。

## 本地运行

依赖 Python 3.13、uv、Node.js、Git、Graphviz 和已登录的 Codex CLI。

```bash
uv sync --frozen
npm --prefix prototype ci
npm --prefix prototype run build
uv run python -m app.main
```

打开 `http://127.0.0.1:8417`。默认分析当前仓库；可用以下环境变量覆盖：

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `SDBP_REVIEW_REPOSITORY` | 当前项目目录 | 只读建模和批准后创建 worktree 的目标 Git 仓库 |
| `SDBP_REVIEW_DATA_DIR` | `data` | SQLite、临时结果和 CLI 内置模型目录 |
| `SDBP_REVIEW_WORKTREE_ROOT` | `data/worktree` | 隔离开发 worktree 根目录 |
| `SDBP_REVIEW_PORT` | `8417` | 本机服务端口 |

容器只用于验证构建、页面和 API，不复制宿主机 Codex 登录态：

```bash
docker compose up --build
```

容器页面位于 `http://127.0.0.1:8418`；需要真实 AI 建模与开发时使用宿主机运行方式。

## 开发方法

1. 自然语言需求先形成统一图中的需求与技术设计差异。
2. 需求方在同一画布逐层审查用户行为、技术设计、代码实现和测试证据。
3. 批准的图 revision 是实现和测试的输入。
4. 实现不得自行修改已批准的需求层与技术设计层。
5. 当前本地开发阶段由固定白名单命令自动验证；全部通过后提交 worktree 并快进合并当前本地分支。
6. 主工作区有变化、基线漂移或验证失败时停止，不提交、不合并。

## 当前设计

- [需求基线](doc/requirement.md)
- [统一交互图模型](doc/graph-model.md)
- [图与代码权威契约](doc/graph-code-contract.md)
- [用户可见流程与需求行为图定义](doc/requirement-graph.md)
- [代码实现层定义](doc/implementation-graph.md)
- [AI Prompt 设计规范](doc/ai-prompt-design.md)
- [第一里程碑技术设计](doc/first-milestone.md)

以下 UML 是统一图的开发投影，只用于辅助阅读和验证，不是可交互模型的数据源：

- [系统上下文](doc/uml/00-context.puml)
- [端到端主流程](doc/uml/01-main-flow.puml)
- [需求状态机](doc/uml/02-requirement-state.puml)
- [核心领域模型](doc/uml/03-domain-model.puml)
- [当前代码建模流程](doc/uml/04-as-is-modeling.puml)
- [代码模型状态机](doc/uml/05-code-model-state.puml)
- [第一里程碑组件图](doc/uml/06-first-milestone-component.puml)
- [第一里程碑时序图](doc/uml/07-first-milestone-sequence.puml)
