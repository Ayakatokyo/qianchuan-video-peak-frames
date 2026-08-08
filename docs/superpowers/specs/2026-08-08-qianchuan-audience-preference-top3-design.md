# 人群洞察核心人群偏好 TOP3 设计

## 目标

在 `02｜人群洞察` 增加确定性的“核心人群偏好 TOP3 素材”外层卡片。它回答“哪类八大人群在各条最终入选素材中占比最高，以及该人群最偏好的三条素材”，不替代现有 AI 人群洞察或“人群真实数据”看板。

## 范围与边界

- 只消费最终 `top_materials_rpa_ready.json` 对应的已保留 `crowd_analysis` 记录；不读取候选池，也不补充或重跑数据。
- 第一部分的 ROI、四维组合结论及其数据契约保持不变。
- 不修改 `scripts/rpa_adapter.py`、`scripts/rpa_batch_runner.py`、`scripts/rpa_runtime.py`、`scripts/rpa_artifact_receipts.py`、`scripts/rpa_csv_parser.py`，也不改变 `standard_workflow` 的 RPA 参数、顺序、状态或产物。
- 不新增指标、阈值、账户配置或 AI 生成的排名。现有年龄、性别、地域和八大人群的展现/点击/成交看板继续保留。

## 确定性数据规则

### 输入

对每条最终入选素材，只读取满足以下条件的记录：

- `recordType == "crowd_analysis"`
- `displayMetric == "SHOW_COUNT"`
- `dimensionKey == "user_group_label_name"`（八大人群）
- 记录归属当前素材，分桶名称非空，且解析出的 `metricValue` 为有限非负数。

同一素材、同一人群分桶的合法记录相加。不得使用上游 `metricPercentage`，不得把点击次数或成交金额代替“最爱看”的展现次数。

### 素材归属

1. 对单条素材的八大人群分桶求和，作为该素材的八大人群展现总量。
2. 总量为零、没有任何合法分桶，或最高值并列时，该素材不进入偏好排名。并列不能任意归属给一个人群。
3. 否则最高展现分桶是该素材唯一的 `audienceGroup`，其偏好占比为 `topBucketValue / totalBucketValue * 100`。
4. 该素材最多归属一个人群，因此不会同时出现在多个群体的偏好列表中。

### 群体 TOP3

按 `audienceGroup` 汇总已归属素材，每个人群按以下稳定键排序，取最多三条：

1. 偏好占比降序；
2. 素材总消耗 `totalCost` 降序；缺失或无效消耗按零参与排序；
3. `materialId` 升序。

输出对象保留人群名称和 `topMaterials`。每条素材包含 `rank`、`materialId`、`materialName`、`audiencePercentage`、`audienceShowCount` 与可用的 `totalCost`。无归属素材的人群不输出空卡片；完全没有可用八大人群数据时输出空列表并由页面显示“暂无核心人群偏好数据”。

## 事实包与报告数据

`diagnostic-analysis-data.json` 的 `audience` 增加 `preferences`：

```json
{
  "preferences": [
    {
      "audienceGroup": "新锐白领",
      "topMaterials": [
        {
          "rank": 1,
          "materialId": "m-1",
          "materialName": "素材一",
          "audiencePercentage": 58.25,
          "audienceShowCount": 5825,
          "totalCost": 1200.5
        }
      ]
    }
  ]
}
```

`final_report_lib` 与 `report_renderer` 原样透传该确定性事实至 `reportData.audience.preferences`。这次不把偏好条目变为 AI 证据引用，也不改变现有 `batchInsights.audience` 的数量、证据或质量门禁。

## 页面与交互

第 02 部分固定为以下顺序：

1. 现有 AI 人群洞察；
2. 新增“核心人群偏好 TOP3 素材”外层卡片，默认展开且不使用收起控件；
3. 现有“人群真实数据”可展开/收起卡片。

偏好卡按有人选结果的人群分组。每组列出最多三条素材，显示序号、素材名称、该人群占比与素材消耗。素材名称是跳转链接，定位到 `04｜单素材深度分析` 内对应 `material-<materialId>` 附录锚点。卡片不重复展示八大人群分布图，也不展示缺失数据、RPA 状态或执行过程。

页面必须在桌面与移动端保持素材名称可换行、数值对齐且链接可键盘访问。没有偏好结果时只渲染一条空状态，不渲染空人群分组。

## 错误处理与兼容性

- 无效记录、素材 ID 不匹配、空分桶、负数和非有限数与现有聚合规则一致地忽略。
- 八大人群最高占比并列时，素材不归属任何人群，避免构造不存在的“最爱看”。
- 缺少 `totalCost` 不阻断偏好事实；它仅作为第二排序键的零值。
- 旧诊断事实包缺少 `audience.preferences` 时，报告可继续渲染并显示空状态；既有 schema v1/v2/v3 的报告行为不改变。

## 测试

1. 事实包测试覆盖单素材占比重算、多个同分桶记录相加、唯一最高分组、忽略非法记录、零总量和并列最高不归属。
2. 排名测试覆盖占比优先、消耗次序、素材 ID 兜底、每组最多三条以及单素材只出现一次。
3. 报告数据测试覆盖 `preferences` 透传和缺字段兼容。
4. 模板测试覆盖偏好卡在 `audience-primary` 后、可折叠 `audience-dashboard` 前，包含单素材锚点链接、空状态和既有人群看板调用。
5. 运行现有人群与最终报告回归；对冻结 RPA 文件的差异检查必须为空。
