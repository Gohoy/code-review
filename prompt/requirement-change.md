# requirement-change@1.2.0

目标：理解当前对话，把新需求作为现有主流程的最小分支写入候选统一图。

1. 读取完整对话与当前图，定位受影响的 Goal、Flow、Scenario、设计节点和代码节点。
   最新一条 USER 消息是本次唯一任务；历史 Review 缺口和 implementation run 状态只能作为背景，不能替换或否定新任务。
   只有当前图已存在用户指定的稳定节点 ID，且其可观察行为和全部验收逐项一致时，才能判断需求已经覆盖。
   最新消息出现 `SCN-*` ID 时，必须先用 `graph_query` 精确查询每个 ID；查无该 ID 就必须以原 ID 新增 Scenario，禁止用语义相似的旧场景替代。
2. 只有答案会改变可观察行为、验收结果、权限、数据或外部依赖时才追问。
3. 信息不足时调用 `graph_create_candidate` 添加 `UNRESOLVED` Question 和 `blocks` 关系，然后返回 `NEEDS_INPUT`。
4. 信息充分时补齐正常路径及真实存在的失败、权限、边界、重复、并发、超时、取消或重试分支；不要机械制造不成立的分支。
5. 技术设计与需求在同一候选 revision 展示，但不得用技术设计偷偷改变需求语义。
6. 信息充分且最新消息新增或修改行为时，必须调用 `graph_create_candidate` 提交最小结构化差异，再调用 `revision_request_approval`。
   当前 revision 已批准、已有失败或待补充的运行，都不妨碍创建它的后代候选；当前 revision 是候选时也必须创建后代候选来表达用户的新修改，不能只对旧候选再次请求批准。

请求批准后返回 `AWAITING_APPROVAL` 并停止；批准前不得调用开发、测试、Review 或合并工具。
