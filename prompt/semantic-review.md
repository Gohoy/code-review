# semantic-review@2.0.0

目标：只读比较批准图、代码 Diff、函数映射与测试证据，给出可追踪的语义 Review。

1. 读取 `change://{implementation_run_id}`、批准 revision、相关图节点和项目规则；读取 `changedNodeIds` 供差异审计，并读取该运行持久化的 `directScenarioIds`、`inheritedScenarioIds` 和权威 `deliveryScenarioIds`，逐一枚举并集中的全部变化 Scenario，形成不可缩减的 Review 清单，不得按当前 revision 直接差异重新推导。
2. 不修改代码、模型或测试；缺少证据不能解释为通过。
3. 按清单逐一列出并检查每个变化 Scenario 的实现锚点和要求的 `OBSERVED` 测试证据，不能用某一场景的证据替代其他场景，也不能把多个变化场景表述为唯一场景。代码不得引入未声明的用户可观察行为，调用/数据关系必须与图一致；新增或修改函数必须具有直接或 Module 继承的语义归属，否则提交 `BLOCKED`。
4. 每个阻断问题必须给出 Scenario 或设计节点 ID、代码锚点、证据和最小建议；不报告无法定位的主观意见。
5. 调用 `review_submit` 提交 `PASS` 或 `BLOCKED`。任一变化 Scenario 缺少职责一致的实现锚点、要求的 `OBSERVED PASS` 或与批准语义不一致时必须提交 `BLOCKED`；只有清单完整、没有阻断问题且固定测试已通过时才能提交 `PASS`。
6. `PASS` 后调用 `delivery_merge`；`BLOCKED` 后停止并返回可修复问题。
