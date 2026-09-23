# 千川 RPA 预检补位设计

## 目标

在正式执行千川 RPA 批量截帧前，提前发现“资产表可选中但 RPA 当前不可获取诊断数据”的素材，并从后续候选中自动补位，尽量在进入正式 RPA 批次前凑满目标 `Top N`。

本设计解决的不是“强判素材已删除”，而是“在当前 RPA 连接器和当前凭据下，该素材是否可成功进入诊断链路”。

## 背景与真实日志结论

真实执行日志显示：

- 资产表在 `2026-06-01` 到 `2026-06-30` 的历史数据中能选出素材 `7646355189088714778`；
- 该素材在正式 RPA 诊断阶段两次失败，且都失败在 `rpaFetchSec`；
- 两次失败都没有落下任何 `diagnosis-csv/<materialId>.csv`、`diagnosis-records/<materialId>.json`、`summary.json` 或 JPEG；
- 同批其他素材在相同账号、相同周期、相同连接器下可以成功完成。

这表明当前问题更适合定义为“RPA 当前不可用”，而不是在无明确状态字段时直接定义为“已删除”。

## 非目标

- 不新增“素材已删除”的强判定字段；
- 不修改资产表选材口径；
- 不在预检阶段下载视频、运行 FFmpeg 或生成帧；
- 不在预检阶段执行完整诊断分析或最终报告。

## 用户可见行为

### 现状

1. 资产表导出 `Top N`；
2. 直接对这 `Top N` 调用正式 RPA；
3. 若其中某条素材无法取回诊断数据，则正式批次部分失败；
4. 即便其余素材成功，`--verify` 仍整体失败。

### 目标行为

1. 资产表先导出大于 `Top N` 的候选池；
2. 在正式 RPA 批次前，对候选素材逐条做轻量 RPA 可用性预检；
3. 预检失败的素材不进入正式批次；
4. 从后续候选中继续补位，直到凑满目标 `Top N`，或候选耗尽；
5. 仅对预检通过后的素材列表执行正式截帧批次。

## 预检通过标准

预检的目标是尽量低成本判断“该素材能否进入正式 RPA 链路”。单条素材必须满足以下条件才视为 `rpaAvailable=true`：

1. 连接器调用成功返回可解析结果；
2. 结果能规范化成记录列表；
3. 结果中存在目标素材的 `material_info` 记录；
4. `material_info.videoUrl` 为非空字符串。

满足以上条件即可通过预检；不要求在预检阶段下载视频，也不要求完成帧提取。

## 预检失败分类

预检失败只定义为“RPA 当前不可用”，细分原因如下：

- `rpa_request_failed`
  - 网关超时、HTTP 错误、网络异常；
- `rpa_payload_invalid`
  - 返回结构不是可解析记录列表，或 files/payload 信封不符合契约；
- `missing_material_info`
  - 返回记录中缺少目标素材的 `material_info`；
- `missing_video_url`
  - 存在 `material_info`，但 `videoUrl` 为空；
- `unknown_preflight_failure`
  - 其他未归类异常。

这些原因都不等价于“素材已删除”。

## 补位策略

### 候选池

当用户要求目标 `Top N` 时，不直接只导出 `N` 条，而是导出更大的候选池 `Top M`。

默认：

- `M = max(N * 3, N + 5)`

例如：

- `Top 3` 的正式目标，对应默认候选池为 `Top 9`

这样既能覆盖少量预检失败，也避免候选池过小导致频繁重新选材。

### 补位流程

1. 按资产表排序顺序读取候选素材；
2. 对每条素材顺序做预检；
3. 预检通过则加入 `selected`；
4. 预检失败则加入 `rejected` 并继续向后补；
5. `selected` 数量达到目标 `Top N` 时停止；
6. 若候选池耗尽仍不足 `Top N`，则整个批次在正式 RPA 执行前中止。

## 中止条件

### 允许继续正式批次

- 成功补足目标 `Top N`

### 必须在正式批次前中止

- 候选池耗尽，仍无法补足目标 `Top N`
- RPA 预检过程本身发生全局性配置问题，例如：
  - 缺失运行时环境变量
  - 无可用 RPA 授权账户
  - `shop_id` 无法解析

### 中止后保留产物

即便提前中止，也必须保留：

- 候选池 `top_materials.json`
- 预检结果文件
- 经过筛选但未达标的中间选择结果
- 明确的失败原因摘要

## 产物设计

### 1. 候选池文件

沿用 `top_materials.json` 结构，但它代表“候选池”，而不是最终正式执行名单。

### 2. 预检结果文件

新增：

- `rpa-preflight.json`

结构至少包含：

- `requestedTopN`
- `candidateCount`
- `shopId`
- `dateRange`
- `selectedMaterials`
- `rejectedMaterials`
- `satisfied`

其中：

- `selectedMaterials` 代表通过预检、将进入正式批次的素材；
- `rejectedMaterials` 记录未通过素材与失败原因；
- `satisfied=false` 表示未能补足目标 `Top N`，正式批次不得开始。

### 3. 最终正式执行名单

新增：

- `top_materials_rpa_ready.json`

它与 `top_materials.json` 结构尽量保持一致，但仅包含通过预检后最终进入正式 RPA 截帧的素材。

正式 `extract_peak_frames.py --source rpa --top-materials-json ...` 只消费这份文件。

## CLI 行为建议

推荐在现有 CLI 内实现，而不是新增独立脚本：

1. `export_top_materials.py`
   - 增加候选池导出能力；
2. `extract_peak_frames.py`
   - 增加预检与补位模式；
   - 或在批处理正式启动前内置预检开关。

推荐新增参数：

- `--target-top-n`
  - 用户真正希望交付的素材数；
- `--candidate-top-n`
  - 候选池大小，默认按策略推导；
- `--preflight-only`
  - 只做预检，不进入正式批次；
- `--rpa-ready-top-materials-json`
  - 预检通过后的正式执行名单路径。

## 代码职责

### `scripts/export_top_materials.py`

- 负责导出候选池；
- 不负责判断 RPA 可用性。

### `scripts/rpa_adapter.py`

- 新增一个轻量预检函数；
- 复用现有网关调用与结果规范化逻辑；
- 不负责候选补位策略。

### `scripts/extract_peak_frames.py`

- 负责批量预检编排；
- 负责候选补位；
- 负责写出 `rpa-preflight.json` 和 `top_materials_rpa_ready.json`；
- 在 `satisfied=false` 时提前中止，不进入正式截帧流程。

## 测试要求

至少覆盖以下场景：

1. 候选池首条素材预检失败，后续素材补位成功；
2. 候选池中多个素材连续失败，但最终仍补足 `Top N`；
3. 候选池耗尽仍不足 `Top N`，正式批次前中止；
4. 预检通过时只要求 `material_info.videoUrl` 存在，不下载视频；
5. 预检失败原因正确落到 `rpa-preflight.json`；
6. 现有正式批量截帧流程在消费 `top_materials_rpa_ready.json` 时不回退。

## 风险与取舍

- 预检本身仍调用 RPA，不能完全消除远端成本；
- 预检成功不等于正式执行必然成功，因为后续仍可能在下载视频或 FFmpeg 阶段失败；
- 但它可以把“连诊断记录都拿不到”的素材尽量前置识别，从真实日志看，这已经能避免当前最典型的整批阻塞。
