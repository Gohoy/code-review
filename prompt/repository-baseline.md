# repository-baseline@3.0.0

目标：为现存仓库建立可审查的行为与代码基线。

1. 首先调用一次 `repository_sync`。它会固定 Git 快照，并确定性写入全部项目函数、可唯一解析的调用关系和已有覆盖率证据；不要用 `graph_create_candidate` 机械重写这些事实。
2. 读取同步后的 `graph://revision/current`，按用户视角识别主流程、现实分支与验收场景；不把目录结构直接当作需求。
3. 使用 `graph_query` 检查已有需求与设计节点，通过 `graph_create_candidate` 为每个 Module 建立至少一条来自需求或技术设计的 `implemented_by` 语义归属；不得用“其他”“杂项”或 Repository 总兜底掩盖职责。
4. Module 归属会传递给其包含的函数；职责混合的模块、用户入口和关键业务函数再建立直接 `implemented_by`。这些关系标记为 `INFERRED`；代码节点、调用关系和覆盖率来源不得改写为 AI 推断。
5. 每个新增 Scenario 必须包含非空 Given/When/Then，并通过 `realized_by` 关联技术设计。
6. 完成前再次读取当前图，确认每个 Module 都有语义归属，并汇报结构归属函数数、语义归属函数数、直接函数归属数、未归属函数数、未解析调用和覆盖率未知；任何缺口都不是自动删除依据。

完成后调用 `revision_request_approval`。若存在无法确认的产品含义，创建 `UNRESOLVED` Question 并返回 `NEEDS_INPUT`。
