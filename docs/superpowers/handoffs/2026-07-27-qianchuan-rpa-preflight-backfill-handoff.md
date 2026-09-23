# 千川 RPA 预检补位交接摘要

## 当前结论

1. 当前技能的素材选择分两段：
   - 资产表按历史周期消耗排序选材；
   - RPA 执行阶段再按素材 ID 取单素材诊断与视频。
2. 资产表能查到素材，并不等于该素材在执行当下仍可从 RPA 成功拿到诊断数据。
3. 真实日志已验证典型案例：
   - 素材 `7646355189088714778` 在资产表中可选中；
   - 但在正式 RPA 执行中两次失败，且都失败在 `rpaFetchSec`；
   - 没有落下任何 `diagnosis-csv`、`diagnosis-records`、`summary` 或 JPEG；
   - 同批其他素材成功，说明问题不是整体配置错误，更像该素材当前 RPA 不可用。

## 已确认的数据事实

### 资产表链路

- 已直接检查素材 `7646355189088714778` 在 `2026-06-01` 的 `origin_data`。
- `origin_data` 中没有明确的删除状态字段：
  - 没有 `deleted`
  - 没有 `is_deleted`
  - 没有 `material_status`
  - 没有通用 `status` 可直接解释为“已删除”
- 因此资产表阶段不能强判“素材已删除”。

### RPA 链路

- 当前 `rpa_adapter.py` 只负责调用连接器并返回记录列表；
- 现有成功素材的诊断记录中可见的主要 `recordType` 有：
  - `material_info`
  - `interaction_series`
  - `delivery_material_detail`
  - `crowd_analysis`
  - `script_analysis`
- 现有成功诊断记录中也未发现明确的删除状态字段。
- 因此 RPA 阶段当前也不能强判“素材已删除”，但可以定义“RPA 当前不可用”。

## 已完成的相关改动

### `shop_id` 自动解析

已在当前分支完成：

- `extract_peak_frames.py` 支持在 RPA 模式未传 `--shop-id` 时自动调用 `list_accounts()`；
- 仅有一个授权账户时自动补全；
- 多账户时报错要求显式传入；
- 新增 `--list-shop-accounts` 只读入口。

对应文档：

- `docs/superpowers/specs/2026-07-27-qianchuan-shop-id-resolution-design.md`
- `docs/superpowers/plans/2026-07-27-qianchuan-shop-id-resolution.md`

## 现阶段推荐方案

不要再直接把资产表的 `Top N` 素材送进正式 RPA 批次。

应改为：

1. 资产表导出更大的候选池；
2. 在正式 RPA 批次前对候选素材做轻量 RPA 预检；
3. 预检通过标准：
   - 连接器成功返回可解析记录；
   - 存在目标素材的 `material_info`；
   - `material_info.videoUrl` 非空；
4. 预检失败的素材不进入正式批次；
5. 从后续候选中继续补位，直到凑满目标 `Top N`；
6. 若候选池耗尽仍无法凑满，则在正式批次前中止。

## 新写好的设计与计划

### Spec

- `docs/superpowers/specs/2026-07-27-qianchuan-rpa-preflight-backfill-design.md`

### Plan

- `docs/superpowers/plans/2026-07-27-qianchuan-rpa-preflight-backfill.md`

## 方案关键点

- 这套方案解决的是“RPA 当前不可用”，不是“已删除强判”；
- 会新增：
  - `rpa-preflight.json`
  - `top_materials_rpa_ready.json`
- 正式 RPA 截帧只消费 `top_materials_rpa_ready.json`
- 如果 `rpa-preflight.json.satisfied=false`，则正式批次不得开始

## 下一步建议

如果在新对话继续推进，直接让新对话读取这三份文件：

1. `docs/superpowers/specs/2026-07-27-qianchuan-rpa-preflight-backfill-design.md`
2. `docs/superpowers/plans/2026-07-27-qianchuan-rpa-preflight-backfill.md`
3. `docs/superpowers/handoffs/2026-07-27-qianchuan-rpa-preflight-backfill-handoff.md`

然后按计划进入实现，重点文件会是：

- `scripts/rpa_adapter.py`
- `scripts/extract_peak_frames.py`
- `tests/test_peak_frames.py`
- `SKILL.md`
- `references/rpa-output-contract.md`
