## 千川 RPA `shop_id` 自动补全设计

### 目标

减少执行千川 RPA 批次前因遗漏 `--shop-id` 导致的失败，同时保留显式查询授权账户的诊断入口。

### 决策

- 保留 `--shop-id` 参数；显式传入时始终优先使用。
- 当 `--source rpa` 且未传 `--shop-id` 时，CLI 自动调用 `rpa_adapter.list_accounts()`。
- 若授权账户列表恰好 1 条，则自动采用该条记录的 `shop_id`。
- 若授权账户列表为 0 条，则报错并提示当前凭据下无可用授权账户。
- 若授权账户列表大于 1 条，则报错并列出账户摘要，要求用户显式传入 `--shop-id`。
- 新增只读入口 `--list-shop-accounts`，打印授权账户与 `shop_id` 后直接退出。

### 边界

- 自动补全只发生在 RPA 模式。
- `--list-shop-accounts` 不要求 `--output-dir`，也不进入素材处理流程。
- 多账户时绝不自动猜测，避免跑错店铺。
- 授权账户查询失败继续沿用 `RpaAdapterError`，CLI 退出码保持 `4`。

### 改动范围

- 修改 `scripts/extract_peak_frames.py`
  - 新增 `--list-shop-accounts`
  - 新增 `shop_id` 解析与账户打印逻辑
  - 放宽 `--output-dir` 解析阶段要求，改为运行时按分支校验
- 保持 `scripts/rpa_adapter.py` 的网关调用逻辑不变，复用现有 `list_accounts()`
- 修改 `tests/test_peak_frames.py`
  - 覆盖单账户自动补全
  - 覆盖多账户拒绝
  - 覆盖显式列出账户
