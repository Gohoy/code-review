# 统一交互图模型

## 1. 结论

`sdbp-review` 不再把“需求 UML”和“技术详情”保存为两套数据。项目只维护一份版本化属性图：

```text
需求层 → 技术设计层 → 代码实现层 → 测试证据层
```

四层共享稳定节点、关系、来源和 revision。浏览器根据用户当前关注的层和节点投影同一份图；Mermaid、PlantUML 和 Graphviz SVG 都只是投影视图，不是事实来源。

当前模型位于 [`model/review-tool.json`](../model/review-tool.json)，结构由
[`model/graph.schema.json`](../model/graph.schema.json) 约束。第一版使用 JSON revision 和 SQLite，不引入图数据库。
图与 Git commit、源码位置及扫描结果之间的权威边界见[图与代码权威契约](graph-code-contract.md)。

## 2. 四个层级

| 层 | 回答的问题 | 典型节点 |
| --- | --- | --- |
| `requirement` 需求 | 用户要做什么、有哪些分支和结果？ | 角色、目标、主线、场景、操作、决策、状态、规则、结果、未知问题 |
| `design` 技术设计 | 准备由什么页面、组件、接口、数据和外部系统实现？ | 页面、组件、接口、数据存储、数据实体、外部系统、运行边界、技术决策 |
| `implementation` 代码实现 | 当前 commit 实际由什么代码实现？ | 仓库、模块、入口、方法、配置、数据表 |
| `verification` 测试证据 | 哪些测试和运行记录证明行为成立？ | 测试、测试运行、观察结果、源码或制品证据 |

技术设计是用户批准对象的一部分，不再只藏在需求节点的属性详情中。代码实现和测试证据只有实际存在时才能加入，不用计划节点伪装成事实。

## 3. 节点

所有节点至少包含：

```json
{
  "id": "DESIGN-COMPONENT-CODEX-RUNNER",
  "layer": "design",
  "kind": "Component",
  "title": "Codex 运行器",
  "summary": "以固定参数调用本机 Codex CLI。",
  "source": "DERIVED",
  "details": {}
}
```

- `id`：不随标题、布局、文件位置变化的稳定 ID；删除后不得复用。
- `layer`：所属层级。
- `kind`：节点在该层的业务类型。
- `title`、`summary`：画布上直接使用自然语言展示。
- `source`：`DECLARED`、`DERIVED`、`OBSERVED`、`INFERRED` 或 `UNRESOLVED`。
- `details`：点击节点后展示的完整场景、接口、命令、数据、源码位置或测试证据。
- `snapshotId`、`anchors`：实现层事实所属的固定代码快照及源码锚点；其他层不得伪造代码位置。

同一节点不能同时代表需求和技术实现。跨层语义通过关系表达，避免一个大节点混合多种可信度和生命周期。

## 4. 关系

关系也是带稳定 ID 和来源的一等数据：

```json
{
  "id": "EDGE-ANALYZE-CODEX-RUNNER",
  "sourceId": "ACTION-ANALYZE-REQUIREMENT",
  "targetId": "DESIGN-COMPONENT-CODEX-RUNNER",
  "kind": "realized_by",
  "source": "DERIVED"
}
```

主要关系分为三组：

- 需求流程：`contains`、`next`、`branch`、`transitions`、`guarded_by`、`produces`、`affects`、`blocks`。
- 跨层追踪：需求到设计用 `realized_by`，设计到代码用 `implemented_by`，需求或实现到测试用 `verified_by`，事实到证据用 `evidenced_by`。
- 技术关系：`exposes`、`calls`、`reads`、`writes`、`invokes`、`creates`、`renders`、`configured_by`、`constrains`。

每条关系的两个端点必须存在。关系类型和两端层级必须通过语义校验；例如 `implemented_by` 不能把代码节点指回需求节点。

实现层的静态技术关系还必须引用 `CodeSnapshot` 和表达式 `CodeAnchor`。这使用户不仅能点击函数节点定位声明，也能点击 `calls`、`reads`、`writes` 或 `invokes` 关系定位实际调用点。代码快照、源码锚点和关系门禁以[图与代码权威契约](graph-code-contract.md)为准。

## 5. 同一画布上的交互

页面仍只有自然语言对话和一张图，但不再把“图”理解成一次性 UML 图片：

1. 默认只显示需求主线，保持用户能快速审查的粒度。
2. 画布提供“需求、技术设计、代码实现、测试证据”四个层级开关。
3. 点击需求节点时，在原位置附近展开与它直接关联的技术设计节点；继续点击设计节点可展开代码和测试节点。
4. 点击任何节点都同步显示其 `summary`、`details`、来源、revision 和关联 Diff。
5. 大图只投影当前主线和焦点邻域；不把全项目所有节点一次铺开。
6. 缩放、坐标、折叠、焦点和临时筛选属于浏览器状态，不进入模型内容哈希，也不产生语义 Diff。

服务根据 `revisionId + layers + focusId` 选择可见子图，再用固定 `dot -Tsvg` 生成带稳定节点 ID 的 SVG。浏览器只负责层级切换、节点点击和详情展示，不允许直接修改图事实。

## 6. 对话实时更新

Codex 不返回 Mermaid、PlantUML 或任意 SVG，而是返回针对当前基础 revision 的结构化图差异：

```text
新增节点 / 修改节点 / 删除节点 ID
新增关系 / 修改关系 / 删除关系 ID
自然语言回复
```

应用按以下顺序处理：

1. 比对 `baseRevisionId`，拒绝基于过期候选产生的结果。
2. 在内存中把差异应用到上一份完整图快照。
3. 校验 JSON Schema、稳定 ID、关系端点、层级规则、来源和完整性门禁。
4. 将完整 `graph` 按 UTF-8、递归键排序、无多余空白且不转义 Unicode 的 JSON 规范化，再计算 SHA-256 内容哈希。
5. 原子保存新的不可变候选 revision，再通知浏览器刷新图与 Diff。

AI 输出无效、Codex 失败或 `dot` 渲染失败时保留上一个有效 revision。未知问题必须成为 `UNRESOLVED` 节点，并用 `blocks` 指向被阻断的需求或设计节点。

## 7. 批准和后续写入边界

“批准并开始开发”同时批准当前 revision 的需求层和技术设计层。批准门禁至少检查：

- 当前浏览器提交的 revision ID 和内容哈希没有过期。
- 图中不存在 `UNRESOLVED` 节点。
- 变化场景具有 Given、When、Then，并至少关联一项技术设计。
- 每项新增或修改的技术设计能追溯到需求场景。
- 每个跨层关系方向合法，所有关系端点存在。

批准后整份 revision 不可变。实现阶段只能读取它并在隔离 worktree 写代码。未来代码扫描和测试完成时，系统创建新的候选证据 revision：保留已批准的需求与设计节点原文，只追加或更新实现层、测试层以及相应追踪关系；若必须修改需求或设计，则停止实现并回到对话重新批准。

## 8. 语义 Diff

Diff 直接比较两个完整图快照：

- 新增：新节点或新关系。
- 修改：同一稳定 ID 的标题、摘要、来源或详情变化。
- 删除：旧 revision 存在而新 revision 不存在。
- 影响：自身未变，但与变化节点存在可追踪关系。

层级开关不会隐藏 Diff 的存在：折叠层有变化时显示数量，展开后在同一画布显示对应变化。布局变化永远不算 Diff。

## 9. 当前候选版本

`REV-REVIEW-TOOL-010` 当前包含：

- 需求层：24 个节点。
- 技术设计层：31 个节点。
- 代码实现层：0 个节点，因为尚未开始开发。
- 测试证据层：0 个节点，因为尚未执行实现验收。
- 关系：102 条。
- 代码快照：0 个，因为本 revision 只冻结契约，尚未执行扫描。
- 未解决问题：0 个。

该候选只冻结图与代码关系的结构和门禁，不用设计节点假装扫描器已经实现。
