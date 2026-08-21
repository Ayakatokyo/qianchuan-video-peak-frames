# 千川元技能阶段一、二设计

## 目标

将现有千川短视频素材分析中的资产查询、RPA 诊断、波次处理、视频高光帧提取与事实包准备能力抽成配置驱动的元技能底座。阶段一、二允许使用者定义受控的素材查询和多指标高光帧规则，但不生成商业分析 Prompt，也不生成新的独立技能包。

## 范围与非目标

本次交付覆盖：

- 复用现有资产筛选、RPA 诊断、波次补位、视频下载、FFmpeg 截帧和事实包生成能力。
- 对当前已确认的资产表维度、原子指标和派生指标提供中文名称的受控查询配置。
- 对六项 RPA 互动指标提供多选、高光帧策略和可追溯选帧证据。
- 为每次查询、每个素材和每张帧保留可机器校验的配置快照与回执。

本次明确不覆盖：

- 将用户的商业分析口语转换为标准化分析 brief 或 AI Prompt。
- AI 视觉理解、AI 结论生成、最终 HTML 报告生成或用户确认界面。
- 为用户输出新的独立技能目录或安装包。
- 接受任意字段名、任意 SQL、任意公式或复杂布尔条件。

## 兼容性策略

现有 `SKILL.md`、`scripts/run_standard_analysis.py` 和当前固定 `CLICK_COUNT` 截帧流程保持行为不变。元技能使用新的入口和配置文件调用同一批底层处理组件；只有新入口启用自定义查询和高光帧策略。

## 总体架构

```text
meta-skill request/config
  -> field registry validation
  -> material query compiler
  -> selected material candidates + query receipt
  -> existing RPA wave collection
  -> highlight selection policy
  -> frame JPEGs + selection evidence
  -> existing fact-package preparation
```

元技能配置是唯一的用户意图输入。所有下游产物都携带配置的规范化副本或其摘要，避免报告、回执和素材清单对同一条件产生不同解释。

## 资产字段注册表

新增版本化注册表，来源是已验证的资产表 schema 与 `artifacts/confirmed_field_mapping.*`。每个条目至少包含：

- 稳定 ID，例如 `overall_cost`、`net_settlement_roi`、`material_type`。
- 面向使用者的中文名称。
- 类型：`dimension`、`atomic_metric`、`derived_metric`。
- 原始字段映射或公式定义。
- 允许的操作符。
- 是否允许用于过滤、排序和事实输出。
- 计算时点：行级维度过滤或按素材周期聚合后计算。

注册表只纳入当前已确认的素材维度和投放指标。派生指标必须在同一素材、同一分析周期内先汇总分子与分母，再计算比率；分母为零时值为不可用，不能作为数值比较或排序值。

## 素材查询契约

查询配置必须包含周期、广告主 ID、Top N、维度条件、指标条件和单个排序定义。条件之间在阶段二只支持 `AND`。

```json
{
  "schemaVersion": 1,
  "period": {"startDate": "2026-08-01", "endDate": "2026-08-07"},
  "advertiserId": "1770096415226951",
  "topN": 9,
  "dimensionFilters": [
    {"field": "material_type", "operator": "in", "value": ["短视频"]}
  ],
  "metricFilters": [
    {"field": "net_settlement_roi", "operator": "gte", "value": 1.5}
  ],
  "sort": {"field": "overall_cost", "direction": "desc"}
}
```

支持的维度操作符为 `eq`、`in`、`contains`、`regex`。支持的指标操作符为 `eq`、`gt`、`gte`、`lt`、`lte`、`between`。注册表决定每个字段实际可用的操作符。

资产表访问仍只使用固定、只读的物理表和日期/广告主范围。查询配置不会被编译为用户提供的 SQL；维度过滤和指标过滤在受控 Python 聚合逻辑中执行。行级维度条件先筛选源行，指标条件在素材聚合和派生指标计算后筛选。输出按排序值、素材 ID 稳定排序。

查询输出除候选素材外必须写入 `query-receipt.json`，包含规范化配置、字段注册表版本、候选数、通过数、被排除数量、排序字段、每条素材的排序值和不可用字段原因。不得写入授权凭证。

## 多指标高光帧契约

高光帧策略允许在一次运行中选择多个指标：

- `CLICK_COUNT`，整体点击次数。
- `LOSE_COUNT`，整体流失数。
- `LIKE_COUNT`，整体点赞次数。
- `COMMENT_COUNT`，整体评论次数。
- `SHARE_COUNT`，整体转发次数。
- `NEW_FOLLOWER_COUNT`，整体新增粉丝数。

每个指标使用自己的策略项：

```json
{
  "schemaVersion": 1,
  "includeZeroSecond": true,
  "policies": [
    {"metric": "CLICK_COUNT", "mode": "local_peaks", "maxFrames": 3},
    {"metric": "SHARE_COUNT", "mode": "global_maximum", "maxFrames": 1}
  ]
}
```

`local_peaks` 合并连续同值平峰为最早秒数，忽略时序首尾点，只保留严格高于相邻点的波峰，按值降序、秒数升序取 `maxFrames` 个。`global_maximum` 在完整时序上取绝对最高值；并列时取最早秒数；阶段二固定 `maxFrames=1`。

所有策略产生的时间点与可选的第 0 秒帧合并后，以精确时间点去重。一张 JPEG 只截取一次，但其帧事实保存全部命中来源。例如同一秒既是点击波峰又是转发最高点时，`selectionEvidence` 同时保留两条命中记录。

每条命中证据包含 `metric`、中文指标名、`mode`、`policyRank`、`durationSec` 与 `metricValue`。单指标无时序数据时，在该素材摘要中写入不可用原因，继续处理其他指标。只有第 0 秒帧关闭或失败，且所有指标均无法选出有效时间点时，该素材后处理失败。

## 状态、恢复与安全

现有波次锁、RPA 回执、下载限制、FFmpeg 降级和本地修复语义继续适用。新增配置在运行开始时被冻结；恢复已有运行时必须读取原始配置快照，禁止用新配置覆盖旧产物。素材 ID、文件路径、JSON schema 和报告就绪校验继续使用现有安全模块。

RPA 连接器当前契约明确保证点击时序。其他五项指标已经被现有诊断事实包识别，但元技能运行前必须对选中素材的返回 records 做可用性检查，并将缺失情况以事实写入回执，不能假定连接器一定返回所有六类曲线。

## 验收与测试

- 字段注册表拒绝未知字段、错误操作符、无效日期、无效广告主和除零派生比较。
- 查询按素材周期聚合后正确处理原子指标、派生指标、过滤、排序与稳定并列顺序。
- 旧标准工作流继续按整体消耗和 `CLICK_COUNT` 局部峰值运行，回归测试不变。
- 六项指标各自能生成局部波峰和全局最高值；平峰、并列、边界点、缺失曲线、跨指标时间去重均有测试。
- 每张 JPEG 的选帧证据可回查到至少一条策略命中或零秒封面规则。
- 查询和高光帧回执不包含会话令牌、Authorization 或视频二进制。

## 后续阶段接口

阶段三将在本设计的规范化查询配置、高光帧策略和事实包之上增加 `analysis-brief.json`，其产物可以成为 AI Prompt 的唯一业务意图来源。阶段四将把已验证的配置、brief、模板和测试样例组装为独立技能包；这两个阶段不改变本设计中的查询与选帧契约。
