# semantic-review@1.0.0

目标：只读比较批准图、代码 Diff、函数映射与测试证据，给出可追踪的语义 Review。

1. 读取 `change://{implementation_run_id}`、批准 revision、相关图节点和项目规则。
2. 不修改代码、模型或测试；缺少证据不能解释为通过。
3. 检查每个变化 Scenario 是否有实现路径和测试证据，代码是否引入未声明的用户可观察行为，调用/数据关系是否与图一致。
4. 每个阻断问题必须给出 Scenario 或设计节点 ID、代码锚点、证据和最小建议；不报告无法定位的主观意见。
5. 调用 `review_submit` 提交 `PASS` 或 `BLOCKED`。只有没有阻断问题且固定测试已通过时才能提交 `PASS`。
6. `PASS` 后调用 `delivery_merge`；`BLOCKED` 后停止并返回可修复问题。
