# 千川技能 2.0 第二部分人群洞察交接摘要

## 状态

`02｜人群洞察` 已完成。本文件保留第二部分的最终业务口径和实现边界，供后续维护使用；后续优化从第三部分交接文件开始：
[2026-08-08-qianchuan-part-03-script-insights-handoff.md](/Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames/docs/superpowers/handoffs/2026-08-08-qianchuan-part-03-script-insights-handoff.md)。

交接基线：`codex/qianchuan-skill-2.0`，提交 `306d85e`（`文档：清理计划文档空白`），已推送至 `origin/codex/qianchuan-skill-2.0`。

第二部分相关提交：

- `8458c05`：定义人群偏好 TOP3 展示方案。
- `58a1cd4`：规划人群偏好 TOP3 实现。
- `acf4cf3`：生成人群偏好 TOP3 事实。
- `a87948f`：渲染人群偏好 TOP3 展示。
- `430be66`：对齐人群偏好导航结构。

## 已完成且不可回退的内容

### 第一部分

`01｜TOP 素材组合结论` 已在 `d7b450c` 完成。它仅使用已验证的最终 Top 素材范围，固定展示净成交 ROI、整体支付 ROI 以及经营表现、人群、脚本、高光画面四维组合结论。后续第三部分及以后改造不得修改第一部分，除非用户明确授权。

### 第二部分

报告第 02 部分依次保留：

1. AI 人群洞察；
2. 默认展开的外层卡片“核心人群偏好 TOP3 素材”；
3. 可展开/收起的“人群真实数据”看板。

偏好素材名称复用既有素材链接行为，点击后激活并定位到 `04｜单素材深度分析` 的对应素材。

对应设计和实现计划：

- [人群偏好 TOP3 设计](/Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames/docs/superpowers/specs/2026-08-08-qianchuan-audience-preference-top3-design.md)
- [人群偏好 TOP3 小计划](/Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames/docs/superpowers/plans/2026-08-08-qianchuan-audience-preference-top3.md)

## 最终业务口径：分支 A

当前采用“核心人群偏好素材 TOP3”的人群中心榜单，而非为每条素材都定义一个展示归属。

1. 只读取最终入选素材的 `crowd_analysis` 记录。
2. 只使用 `SHOW_COUNT` 和八大人群维度 `user_group_label_name`。
3. 同一素材、同一人群的合法展现次数相加；不使用上游 `metricPercentage`，不使用点击或成交数据替代。
4. 一个素材仅在其八大人群展现最高值唯一时归属该人群；最高值并列、无合法分桶或总量为零时不归属任何人群。
5. 各人群内按“该人群占比降序、素材 `totalCost` 降序、`materialId` 升序”排序，最多展示 3 条。
6. 某人群不足 3 条时只展示实际数量，不用其他人群补齐；没有结果的人群不渲染空卡片。

因此，它回答的是“每个核心人群最偏好的素材有哪些”，不是“每个素材最偏好的人群是谁”。在一批素材大量唯一归属同一人群时，页面少于 3 个素材/人群是预期行为，不是数据缺失。

用户已确认保留此分支 A，当前不再修改第二部分。

## 预留分支 B

未来可能实现“TOP 素材核心人群归属”：为每条 Top 素材显示其唯一的最高偏好人群，以回答“每个素材最喜欢的人群是谁”。该功能尚未设计或实现。

本地与远端均已建立预留分支 `codex/audience-preference-top3-presentation`。在本次交接基线中，它与 `codex/qianchuan-skill-2.0` 指向同一提交；不得据此推断分支 B 已有实现。

## 现有实现位置

- `scripts/diagnostic_analysis_lib.py`
  - `PREFERENCE_METRIC = "SHOW_COUNT"`
  - `PREFERENCE_DIMENSION = "user_group_label_name"`
  - `PREFERENCE_LIMIT = 3`
  - `_aggregate_audience_preferences()` 生成 `diagnostic["audience"]["preferences"]`。
- `scripts/final_report_lib.py`
  - 将完整的 `audience` 事实透传至 `reportData.audience`。
- `templates/final-report-template.html`
  - `#audience-preferences` 位于 `#audience-primary` 之后、可折叠 `#audience-dashboard` 之前。
  - `renderAudiencePreferences()` 处理分组、空状态和素材跳转。
- `tests/test_diagnostic_analysis.py`、`tests/test_final_report.py`
  - 覆盖素材归属、并列排除、稳定排序、透传与模板位置。

## 不可触碰的边界

未经用户明确授权，不得修改下列 RPA 模块：

- `scripts/rpa_adapter.py`
- `scripts/rpa_batch_runner.py`
- `scripts/rpa_runtime.py`
- `scripts/rpa_artifact_receipts.py`
- `scripts/rpa_csv_parser.py`

同时不得改变 `scripts/standard_workflow.py` 中 RPA `batch_arguments` 的构造、顺序或语义。不得改变 RPA 请求、批次状态、产物、恢复逻辑、回执或调用顺序。

## 验证基线

第二部分完成时已执行：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

命令退出码为 `0`。冻结 RPA 文件差异检查无输出。此前记录的“337 项测试”已过时，不再作为当前测试数量基线。
