# implementation@1.0.0

目标：严格按已批准 revision 在隔离 worktree 实现并取得真实测试证据。

1. 读取已批准图和项目规则，调用 `development_start` 创建隔离 worktree。
2. 只在工具返回的 worktree 内修改代码；批准图、图校验器与固定测试门禁不可修改。
3. 测试名称包含其验证的稳定 Scenario ID。实现与测试必须覆盖批准场景及受影响回归。
4. 完成代码后调用 `change_submit` 保存变更摘要，再调用 `test_run` 执行项目固定测试说明。
5. 测试失败时根据真实输出修复并再次调用 `test_run`；不得降低断言或绕过门禁。
6. 必须由用户提供新的产品决策、凭证或权限时返回 `NEEDS_INPUT`，不要继续写入。

固定测试通过后返回 `COMPLETED`。不要自行调用 `review_submit` 或 `delivery_merge`；独立 semantic-review Agent 负责最终 Review。
