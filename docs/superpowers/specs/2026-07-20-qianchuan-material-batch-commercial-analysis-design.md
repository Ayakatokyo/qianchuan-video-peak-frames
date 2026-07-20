# 千川批量素材高光与曝光人群商业分析设计

## 目标

将现有单素材点击峰值截帧能力扩展为本地批量分析工作流。工作流对指定统计周期内、按素材名称和类型筛选出的素材，按整体消耗选择 TOP 素材，提取高光画面，生成可人工确认的画面分类，并输出高光分类、曝光年龄和八大人群的商业分析。

本期不输出转化人群结论。当前连接器仅明确提供曝光口径的人群维度；素材整体的转化、成交和 ROI 仅作为素材表现指标。

## 已确认决策

| 主题 | 决策 |
| --- | --- |
| 当前数据源 | 本地批量单素材诊断 JSON 和对应本地视频 |
| 周期口径 | 使用每条记录的 `actualStartDate`、`actualEndDate`；不得将请求周期替代实际生效周期 |
| 素材筛选 | `material_info.materialName` 正则筛选；可选按创建时间和素材标签筛选 |
| TOP 素材排序 | 按 `delivery_material_detail.totalCost` 按素材汇总后降序，默认 TOP 20 |
| 高光规则 | 复用现有 `CLICK_COUNT` 首个时间峰值和其后点击 TOP 3 的确定性规则 |
| 高光分类 | 混合模式：自动候选、人工确认、只统计已确认分类 |
| 曝光人群 | 年龄和八大人群，基于 `crowd_analysis` 的原始曝光量重新计算占比 |
| 转化人群 | 不纳入本期；不得从素材整体转化字段推导人群结构 |
| 人群归因边界 | 人群数据精确到素材，不精确到单帧；报告明确为素材级相关性，非单帧因果归因 |
| 线上采集 | 不在本期实现；现有单素材 RPA 与未来列表连接器均可复用本地批量管线 |

## 输入契约

`prepare` 阶段读取一个目录中的单素材诊断 JSON。每个 JSON 都是连接器输出的 `List[Dict]`，并通过 `recordType` 区分以下记录：

| 记录 | 本期用途 | 必需字段 |
| --- | --- | --- |
| `material_info` | 素材筛选、视频匹配、分类候选依据 | `materialId`、`materialName`、`actualStartDate`、`actualEndDate` |
| `interaction_series` | 选择点击高光帧 | `materialId`、`interactionMetric=CLICK_COUNT`、`durationSec`、`metricValue` |
| `crowd_analysis` | 曝光年龄、八大人群 | `materialId`、`displayMetric=SHOW_COUNT`、`dimensionKey`、`metricValue`、`dimensionTotal` |
| `delivery_material_detail` | 周期消耗和商业指标 | `materialId`、`statDate`、`totalCost`、CTR、转化率、成交、ROI 字段 |
| `script_analysis` | 分类候选依据 | `materialId`、`scriptInfo` |

视频通过文件名安全化的 `materialId` 匹配，或通过可选视频映射表显式提供 `materialId` 到文件路径的映射；未来可由 `material_info.videoUrl` 下载后复用同一接口。缺失或歧义匹配不得猜测。

## 工作流

```text
诊断 JSON 批次 + 视频批次
  -> 校验每个素材的实际统计周期和记录一致性
  -> 按名称正则、可选创建时间和素材标签筛选
  -> 聚合 totalCost，按消耗降序取 TOP N
  -> 复用单素材峰值算法和 FFmpeg 截帧
  -> 生成分类候选和 review.csv
  -> 人工确认素材主高光标签
  -> 聚合已确认标签、曝光年龄和八大人群
  -> 写入分析 JSON、Markdown 商业报告与告警
```

### 阶段一：prepare

```bash
python scripts/analyze_material_batch.py prepare \
  --input-dir "/path/to/diagnosis-jsons" \
  --video-dir "/path/to/videos" \
  --output-dir "/path/to/output" \
  --name-regex "六号店.*50g" \
  --top-n 20
```

此阶段筛选、排名、截帧并生成可编辑的 `review.csv`。输入记录的实际周期必须一致，否则整体失败。输出包含每个入选素材的峰值帧及单素材摘要。

### 阶段二：report

```bash
python scripts/analyze_material_batch.py report \
  --prepared-dir "/path/to/output" \
  --review-file "/path/to/output/review.csv"
```

此阶段读取人工确认后的分类，生成聚合结果和报告。只接受已确认的素材主高光标签，未确认帧和素材不得进入正式分类统计。

## 分类与人工确认

初始分类字典放在可编辑配置中，包含：

- `产品特写`
- `人物口播`
- `使用/食用演示`
- `功效/前后对比`
- `场景/生活方式`
- `价格/促销`
- `图文/证据背书`
- `其他/待确认`

候选标签仅基于可追溯的素材标题、脚本、`myTagEntry`、`benchTagEntry` 和配置关键词生成。候选依据必须写入 `review.csv`；规则不能确定时标签为 `其他/待确认`。本期不把不可追溯的模型判断作为正式结论。

`review.csv` 每行对应一个高光帧，至少包含：素材 ID、素材名称、帧路径、峰值角色、峰值秒数、点击数、候选标签、候选依据、人工主标签、辅助标签、确认状态和 `isMaterialPrimary`。每个素材还必须有且仅有一条已确认的 `isMaterialPrimary=true` 记录；该记录的人工主标签即“素材主高光标签”。生成候选时默认标记最早高光帧为主帧，但必须由人工确认后才生效。

## 聚合与报告口径

### 高光分类

按素材主高光标签汇总：高光帧数、素材数、总消耗、消耗占 TOP 素材总消耗的比例、整体展现、整体点击、成交订单和成交金额。分类 CTR 重新计算为“分类内整体点击 / 分类内整体展现”，ROI 重新计算为“分类内成交金额 / 分类内总消耗”。连接器未定义分类层面转化率的统一分母，因此报告展示入选素材的转化率明细和中位数，不计算或声称一个分类汇总转化率。辅助标签可用于明细检索，不得参与互斥主分类占比。

### 曝光人群

仅处理 `recordType=crowd_analysis` 且 `displayMetric=SHOW_COUNT` 的记录。

- 年龄按 `dimensionKey=age` 聚合。
- 八大人群按连接器实际输出的对应 `dimensionKey` 聚合。
- 各桶曝光量为原始 `metricValue` 的和。
- 分类内占比为“分类内桶曝光量之和 / 分类内所有该维度桶曝光量之和”。
- 禁止对单素材 `metricPercentage` 做平均。
- 缺少对应维度记录的素材进入缺失清单，且不进入该维度分母。

同一素材的人群数据不在多个高光帧分类间分摊，以避免把素材级数据错误归因给单帧。报告必须标注：结果用于描述“具备该主高光分类的素材所触达的人群”，不是该帧直接带来的曝光。

### 商业报告

`report.md` 至少包含：

1. 筛选周期、名称规则、入选数、失败数和 TOP 素材消耗覆盖率。
2. 高光分类的帧数、素材数、消耗和素材整体商业指标。
3. 每个已确认分类下的曝光年龄结构和八大人群结构。
4. 数据缺失、未确认标签、无法截帧和周期异常的告警。
5. 素材级相关性和“无转化人群数据”的方法限制说明。

同时输出 `analysis.json`，包含报告的数值、分子、分母、所用素材 ID 和路径，确保每一项结论可追溯。

## 输出目录

```text
output/
├── materials/
│   └── <material-id>/
│       ├── first_peak_*.jpg
│       ├── later_top_*.jpg
│       └── summary.json
├── review.csv
├── failures.json
├── warnings.json
├── analysis.json
└── report.md
```

## 失败和告警策略

单个素材出现缺视频、无峰值、无消耗明细或缺少人群维度时，不阻止其他合格素材分析。系统记录到 `failures.json` 或 `warnings.json`，且缺失人群不参与相应分母。

以下情况整体失败，不发布最终分析目录：

- 没有任何合格素材。
- 输入记录结构冲突、同一素材周期不一致或批次实际周期不一致。
- 输出目录已存在。
- `report` 阶段的确认表无法解析，或同一素材存在冲突的已确认主高光标签。

采用暂存目录和原子发布，避免留下看似成功的半成品。

## 测试策略

新增测试必须覆盖：

- 名称、标签、创建时间和实际周期筛选。
- `totalCost` 解析、分日汇总、并列排序与 TOP N。
- 单素材峰值逻辑的回归兼容。
- 多素材截帧、视频匹配和单素材失败后继续处理。
- 候选标签依据、人工确认解析和主标签冲突。
- 年龄与八大人群的原始曝光量聚合、分母重算、未确认与缺失记录排除。
- `analysis.json` 与 `report.md` 的指标、追溯清单和方法限制。
- 目标目录冲突、输入周期不一致和原子发布。
