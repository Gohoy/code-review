# repository-baseline@1.0.0

目标：为现存仓库建立可审查的行为与代码基线。

1. 调用 `repository_index` 获取固定 Git 快照中的项目自有函数、入口和当前映射覆盖率。
2. 按用户视角识别主流程、现实分支与验收场景；不把目录结构直接当作需求。
3. 使用 `graph_query` 检查已有节点，只提交最小差异，保留稳定 ID。
4. 调用 `graph_create_candidate` 写入候选图。函数事实必须包含扫描器返回的 snapshot 与 anchor；函数到需求或设计的语义关系标记为 `INFERRED`。
5. 每个新增 Scenario 必须包含非空 Given/When/Then，并通过 `realized_by` 关联技术设计。
6. 未映射函数不是自动删除依据；将其作为覆盖缺口汇报。

完成后调用 `revision_request_approval`。若存在无法确认的产品含义，创建 `UNRESOLVED` Question 并返回 `NEEDS_INPUT`。
