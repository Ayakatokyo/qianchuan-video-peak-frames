# 千川技能 2.0 第三部分脚本洞察交接摘要

## 当前状态

`01｜TOP 素材组合结论` 与 `02｜人群洞察` 已完成并提交。下一步是 `03｜脚本洞察` 的需求澄清、设计确认和独立小计划；本交接不授权直接修改第三部分实现。

交接基线：`codex/qianchuan-skill-2.0`，提交 `306d85e`（`文档：清理计划文档空白`），已推送至 `origin/codex/qianchuan-skill-2.0`。创建本交接前工作树无业务代码改动。

先阅读：

- [整体 2.0 改造设计](/Users/shuzida/Desktop/FDE客户/小仙炖/千川技能2.0改造设计.md)
- [第一部分设计](/Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames/docs/superpowers/specs/2026-08-08-qianchuan-top-material-dimension-conclusions-design.md)
- [第二部分人群偏好设计](/Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames/docs/superpowers/specs/2026-08-08-qianchuan-audience-preference-top3-design.md)

## 已完成且不可回退的内容

### 第一部分

`01｜TOP 素材组合结论` 已在 `d7b450c` 完成。它使用最终验证的 Top 素材生成确定性经营事实，固定展示两张 ROI 卡以及经营表现、人群、脚本、高光画面四张组合结论卡。后续工作不得修改第一部分，除非用户明确授权。

### 第二部分

`02｜人群洞察` 已在 `8458c05`、`58a1cd4`、`acf4cf3`、`a87948f`、`430be66`、`306d85e` 完成。其新增的“核心人群偏好 TOP3 素材”是人群中心榜单：仅将八大人群 `SHOW_COUNT` 唯一最高的最终入选素材分配给该人群，再在各群内展示最多三条。用户已确认保留该分支 A，当前不再调整第二部分。

未来可能的分支 B“TOP 素材核心人群归属”尚未实现；预留分支 `codex/audience-preference-top3-presentation` 此刻与当前分支同指向，不包含额外功能。

## 第三部分的整体设计要求

整体设计第 3.1 节与第 10.1 节确认 `03｜脚本话术看板` 应当：

1. 按脚本标签分类汇总话术；
2. 展示标签出现次数和素材覆盖数；
3. 展示代表话术及其对应素材跳转；
4. 将单素材脚本原文与标签保留在附录。

来源跳转应复用附录锚点 `material-<materialId>`，而不是新建一套素材定位机制。

整体设计还描述了未来“高光帧与脚本段”的时间映射，但它依赖带 `startSec`、`endSec` 的 `scriptSegments`。当前输入是否提供此能力尚未确认。没有可靠时间戳时，必须保留整段脚本并将高光帧话术映射标记为不可用；不得由 AI 或规则从整段文案猜测帧时刻的话术。该映射不是本次第三部分启动的默认范围。

## 当前实现现状

### 确定性脚本事实

`scripts/diagnostic_analysis_lib.py` 已有：

- `_parse_script_info()`：解析结构化 `scriptInfo`；
- `_script_labels_and_evidence()`：从 `data`、`detail` 提取一级/二级标签与去重证据句；
- `_build_scripts()`：基于最终入选素材的 `script_analysis` 记录构造：
  - `scripts.materials`：每条素材的正文、标签、证据句与素材 URI；
  - `scripts.labelSummary`：按一级/二级标签汇总的 `occurrenceCount`、去重 `materialIds` 和证据句。

当前 `occurrenceCount` 是标签在素材脚本分析记录中出现的次数，`materialIds` 是覆盖素材集合。新设计必须明确它们是否正好等于报告想要的“标签出现次数”和“素材覆盖数”，不要重算或改名后改变其语义。

### 报告数据与页面

`scripts/final_report_lib.py` 已有 `_script_formula_from_record()`、`_scripts_by_material()`、`_script_records_by_material()`、`_material_script_analysis()`，并将脚本事实传入 `reportData.scripts`，也为每条 Part 4 素材提供 `scriptAnalysis`。

`templates/final-report-template.html` 现有：

- 第 03 部分：`#script-primary` 和 `#script-dashboard`；
- `renderScriptDashboard()`：按一级标签聚合 `occurrenceCount`，只渲染“内容公式一级标签分布”柱状图；
- Part 4：单素材脚本公式、标签、证据句与原文展示。

这说明部分输入链路已经存在，但第 03 部分尚未明确满足“二级标签分组、素材覆盖数、代表话术、素材跳转”的完整看板要求。设计时应避免把 Part 4 的原始脚本内容重复堆进核心板块。

### AI 契约与测试

`prompts/diagnostic-analysis-prompt-v1.md` 要求：

- `batchInsights` 中至少输出 2 条 `topic: "script"` 洞察；
- 第 03 部分使用这些 AI 洞察；
- 不允许 `topic: "integrated"` 或 `topic: "highlight"`；
- 组合层 `portfolioDimensionConclusions.script` 不得与第 03 部分的洞察原文完全相同。

`scripts/final_report_lib.py` 也会校验这些最少数量与证据引用。相关回归集中在 `tests/test_diagnostic_analysis.py` 与 `tests/test_final_report.py`。

## 设计确认前必须澄清的口径

先读取上述实现和整体设计，再向用户逐项确认至少以下问题：

1. 主看板是复用并扩展现有 `#script-dashboard`，还是改为专用的“脚本标签分组”面板；两者都必须保留第 03 部分的 AI 洞察入口。
2. 标签分组展示一级标签、一级/二级组合，还是二者分层展示；其排序与同值处理规则是什么。
3. “代表话术”的选择规则：每个标签展示几句，是否按输入顺序、首次出现、素材消耗或其他确定性规则选择；不得让 AI 从原文中自行归纳并冒充确定性代表句。
4. “素材覆盖数”应由去重 `materialIds` 的数量计算，还是另有业务定义；页面是否同时展示出现次数。
5. 何时显示素材跳转，以及素材名称、ID、脚本句与标签之间的关联展示方式；跳转必须指向现有 Part 4 素材锚点。
6. 本轮是否只处理现有无时间戳脚本事实。除非数据源已确认提供分段时间戳，否则帧-话术映射继续明确排除在范围外。

在用户确认设计前，不新增指标、账户配置、脚本时间映射、Prompt 契约或报告结构改动。

## 不可触碰的边界

未经用户明确授权，不得修改：

- `scripts/rpa_adapter.py`
- `scripts/rpa_batch_runner.py`
- `scripts/rpa_runtime.py`
- `scripts/rpa_artifact_receipts.py`
- `scripts/rpa_csv_parser.py`

同时不得改变 `scripts/standard_workflow.py` 中 RPA `batch_arguments` 的构造、顺序或语义，也不得改变 RPA 请求、批次状态、产物、恢复逻辑、回执或调用顺序。

## 后续执行顺序

1. 仅做第三部分现状审计，核实 `scriptInfo` 的真实字段结构、`labelSummary` 统计语义、已有模板行为和测试覆盖。
2. 基于用户确认，提出可选设计并等待批准。用户已说明不使用可视化辅助，设计沟通保持文本形式。
3. 获批准后，在 `docs/superpowers/specs/` 写入并提交第三部分设计；用户审阅该文档后，再建立 `docs/superpowers/plans/` 中独立的小计划。
4. 实现必须测试先行，并在完成前运行针对性测试、完整回归、`git diff --check` 与冻结 RPA 文件差异检查。

## 验证基线

第二部分完成时曾执行：

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

退出码为 `0`；冻结 RPA 文件差异检查无输出。测试总数不以历史记录为准，开始第三部分前应重新执行并记录当时结果。
