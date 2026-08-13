# requirement-change@1.0.0

目标：理解当前对话，把新需求作为现有主流程的最小分支写入候选统一图。

1. 读取完整对话与当前图，定位受影响的 Goal、Flow、Scenario、设计节点和代码节点。
2. 只有答案会改变可观察行为、验收结果、权限、数据或外部依赖时才追问。
3. 信息不足时调用 `graph_create_candidate` 添加 `UNRESOLVED` Question 和 `blocks` 关系，然后返回 `NEEDS_INPUT`。
4. 信息充分时补齐正常路径及真实存在的失败、权限、边界、重复、并发、超时、取消或重试分支；不要机械制造不成立的分支。
5. 技术设计与需求在同一候选 revision 展示，但不得用技术设计偷偷改变需求语义。
6. 调用 `graph_create_candidate` 提交最小结构化差异，再调用 `revision_request_approval`。

请求批准后返回 `AWAITING_APPROVAL` 并停止；批准前不得调用开发、测试、Review 或合并工具。
