# 千川素材汇总全字段查询设计

## 目标

扩展当前技能的素材列表查询，使其在单次、指定日期范围的请求中返回：

- 9 个已确认的非时间分组维度；
- 102 个已验证可由报表接口返回的指标；
- 每条记录代表一个素材在整个查询日期范围内的汇总值，不按日期或小时拆分。

“全字段”在本设计中严格指已知字段全集：动态 config 的 98 个指标，加上 4 个已直接验证可返回的 overall 指标。它不表示服务端所有未公开字段的理论全集。

## 范围与非目标

包含：

- 修改 `scripts/qianchuan_report_client.py` 的默认素材报表字段契约。
- 保持现有的单次网关请求、认证、文件下载、CSV 结构化和记录返回方式。
- 对响应字段完整性进行校验，并在运行目录留下不含凭证的查询字段清单。
- 为新的字段契约和异常路径补充单元测试。

不包含：

- 调用 `get-qianchuan-report-config` 或迁移提交的巨量千川投放工具。
- 探查或承诺未验证的服务端指标。
- 按日、按小时结果，或为这些不同粒度结果做跨查询合并。
- 改变既有素材筛选、overall 排序和下游记录格式。

## 固定字段契约

### 维度

查询使用以下 9 个维度，少于接口单次最多 10 个维度的限制：

```text
ecp_app_id
roi2_material_type_v3
roi2_material_video_play_info
roi2_material_video_name
roi2_material_video_type
roi2_material_advice_reason
material_create_time_v2
material_id
anchor_id
```

明确不请求以下 3 个 config 维度：

```text
roi2_material_advice
stat_time_day
range_stat_time_hour
```

其中 `stat_time_day` 和 `range_stat_time_hour` 会把结果拆分为素材乘日期或素材乘小时，违背素材日期范围汇总的目标。`roi2_material_advice` 按业务选择排除。

`ecp_app_id` 与 `roi2_material_type_v3` 同时出现在筛选条件和维度中是有意设计：筛选限定有效记录范围，维度保留该记录的实际属性。

### 指标

默认指标集合由两部分组成：

```text
98 个动态 config 已列出的指标
+ 4 个已验证的扩展 overall 指标
= 102 个指标
```

4 个扩展指标固定为：

```text
stat_cost_for_overall_roi2
total_prepay_and_pay_settle_overall_roi2_1h
total_cost_per_pay_order_settle_for_overall_roi2_1h
total_prepay_and_pay_settle_roi2_1h
```

102 个字段以静态、有序、无重复的 Python 常量表示。实现时保留现有已请求的 86 个指标，并补充 config 中已确认缺失的 16 个播放、互动与流量指标：

```text
live_show_count_for_roi2_v2
live_cvr_rate_for_roi2_v2
live_watch_count_for_roi2_v2
live_convert_rate_for_roi2_v2
total_cpc_for_roi2
total_ecpm_for_roi2
video_avg_watch_duration_for_roi2
video_comment_count_for_roi2_v2
video_follow_count_for_roi2
video_like_count_for_roi2
video_play_count_for_roi2_v2
video_play_duration_2s_rate_for_roi2
video_play_duration_3s_rate_for_roi2
video_play_duration_5s_rate_for_roi2
video_play_duration_10s_rate_for_roi2
video_play_finish_rate_for_roi2_v2
```

## 数据流与组件边界

1. `build_business_params` 根据广告主、日期范围和静态字段常量构造业务参数。现有视频素材筛选和按 `stat_cost_for_roi2` 降序的排序保持不变。
2. `_fetch_gateway_result` 使用现有网关协议发起一次请求，并保留已脱敏的网关响应与下载文件。
3. `_records_from_result` 和 CSV 结构化逻辑维持现有 `{"dimensions": ..., "metrics": ...}` 记录协议，不重命名、不计算、不合并指标。
4. 新增的字段契约校验发生在记录结构化之后、返回给下游之前。它只校验键是否被接口返回，不把 `0`、空字符串或 `null` 当作缺字段。
5. 成功时，在 `source-data` 中写入不含认证信息的字段清单（数据主题、9 个维度、102 个指标、筛选、排序、日期范围和记录数），使一次运行可审计。

下游只依赖既有 `material_id`、`dimensions`、`metrics` 结构，因此扩展字段仅增加可用元数据和事实，不改变排名或素材识别方式。

## 完整性与失败处理

- 构造请求前验证：维度数量必须小于等于 10；维度和指标不得重复；时间粒度维度不得出现在素材汇总契约中；排序字段必须属于指标集合。
- 响应验证：若存在记录但任一记录缺少契约中的维度或指标键，抛出 `ReportClientError`。错误中只列字段名和缺失数量，不包含认证信息、原始请求或敏感 URL。
- 空结果继续沿用现有 `material report contains no records` 错误。
- 下载、网关和 CSV 格式错误继续使用现有错误路径；不得因动态 config 不可用而回退或删减字段，因为方案 1 不依赖 config。
- 字段清单只在成功完成字段校验后落盘，避免将不完整结果标识为全字段数据。

## 测试策略

更新 `tests/test_qianchuan_report_client.py`，覆盖：

- 默认维度恰为 9 个，并包含所有指定元数据维度，不包含 `roi2_material_advice`、`stat_time_day`、`range_stat_time_hour`。
- 默认指标恰为 102 个、无重复，包含 4 个扩展 overall 指标与 16 个新增播放/互动/流量指标。
- 构造的请求仍保留日期范围、筛选、排序和现有认证协议。
- 正常 payload 和文件型 CSV 响应通过字段校验并保留既有记录协议。
- 缺少任一预期维度或指标的非空记录会产生不暴露凭证的 `ReportClientError`。
- 成功运行写入的字段清单含 9/102 数量、查询范围与记录数，不含 session、authorization、cookie 或带敏感查询参数的 URL。

不进行真实线上请求作为单元测试前提；字段可用性已通过用户提供的内网查询结果验证。实施完成后，以模拟网关测试验证契约，并可在具备授权的内网环境用同条件查询作人工验收。

## 验收标准

- 单次素材报表请求只使用 9 个非时间维度，且返回素材日期范围汇总粒度。
- 请求中包含 102 个已知指标，覆盖已确认的前端 99 个指标语义。
- 不再因动态 config 缺少 4 个 overall 指标而减少当前技能返回的字段。
- 下游仍能消费相同的规范化记录结构，现有排序逻辑不变。
- 字段缺失不能静默通过，并且运行产物可说明实际请求的字段集合。
