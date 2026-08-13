# repository-baseline@2.0.0

目标：为现存仓库建立可审查的行为与代码基线。

1. 首先调用一次 `repository_sync`。它会固定 Git 快照，并确定性写入全部项目函数、可唯一解析的调用关系和已有覆盖率证据；不要用 `graph_create_candidate` 机械重写这些事实。
2. 读取同步后的 `graph://revision/current`，按用户视角识别主流程、现实分支与验收场景；不把目录结构直接当作需求。
3. 使用 `graph_query` 检查已有需求与设计节点，只通过 `graph_create_candidate` 补充最小的语义关系差异，保留稳定 ID。
4. 函数到需求或设计的 `implemented_by` 关系标记为 `INFERRED`；代码节点、调用关系和覆盖率来源不得改写为 AI 推断。
5. 每个新增 Scenario 必须包含非空 Given/When/Then，并通过 `realized_by` 关联技术设计。
6. 未映射函数、未解析调用和覆盖率未知不是自动删除依据；将其作为明确缺口汇报。

完成后调用 `revision_request_approval`。若存在无法确认的产品含义，创建 `UNRESOLVED` Question 并返回 `NEEDS_INPUT`。
