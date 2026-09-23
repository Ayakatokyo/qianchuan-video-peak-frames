# 千川任务内商业分析与 Tab 报告设计

## 目标

在已完成的 TOP 素材选择、RPA 诊断、视频高光帧提取和批次验收之后，由当前执行该技能的模型在同一次任务内完成真实商业分析。模型必须实际查看导出的 JPEG 高光帧，结合确定性事实包生成可验证的结构化结论，再渲染最终 HTML 报告。

本设计不在 Python 脚本内接入任何模型 API。执行技能的模型可以是 Codex 或任何具备文件读取与图片查看能力的兼容模型。

首期分析范围为：

- 人群：严格分开曝光覆盖（`SHOW_COUNT`）、点击吸引（`CLICK_COUNT`）和成交贡献（`TRANSACTION_AMOUNT`）。
- 脚本：使用已有脚本标签、原文和标签证据识别内容模式。
- 高光帧：查看实际 JPEG，记录画面观察，并与峰值秒数和点击值关联。
- 两层结论：TOP 素材组合级洞察与每条成功素材的独立诊断、决策和下一步。

## 非目标

- 不修改 RPA 请求、候选补位、视频下载、峰值选择和批次验收的行为。
- 不将模型密钥、模型供应商、网络调用或重试策略耦合进 Python 报告脚本。
- 不将素材级相关性或峰值帧同时性写成因果归因。
- 不把已有报告改造成新的页面风格或外部 Web 应用。

## 基线与报告结构

实现以 `runs/2026-07-28-top9-last6-1770096415226951-rerun2/final-report/final-report.html` 为 HTML 基线，保留其深色双栏 Hero、代表高光帧、浅色内容区、卡片视觉语言和静态单文件交付方式。

移除现有 `01｜组合总览` 的五张统计卡（素材数、成功抽帧、高光帧、总消耗、告警）。Hero 后直接呈现组合结论。最终章节按以下顺序渲染：

1. `01｜TOP 素材组合结论`：组合级商业判断、最优先机会与行动。
2. `02｜人群洞察`：分别呈现曝光、点击、成交三层的人群结论与证据；不得混写。
3. `03｜脚本洞察`：高频脚本模式、覆盖素材和原文/标签证据。
4. `04｜高光帧洞察`：跨素材的视觉观察、峰值时间和点击上下文。
5. `05｜单素材深度分析`：Tab 化查看每条素材的完整诊断。
6. `06｜下一轮策略`：按优先级排序的组合级行动。
7. `07｜数据质量与方法说明`：保留告警、缺失输入与归因边界。

单素材区域不再纵向重复渲染全部素材卡。它使用原生 Tab：默认打开排名最高的可用素材；每个 Tab 显示该素材的高光帧网格、人群与脚本证据、高光帧视觉观察、综合判断、投放决策和下一步。所有素材的内容均预渲染到静态 HTML 中，不发起网络请求。交互使用 `button`、`role="tablist"`、`role="tab"` 和 `role="tabpanel"`，并支持点击、左右方向键、Home/End 键和焦点状态。

## 架构

```text
RPA 诊断记录 + TOP 素材 + 峰值 JPEG
                 |
                 v
        确定性事实包（Python）
        - 人群三层汇总、脚本、投放事实
        - 高光帧索引和文件路径
                 |
                 v
   当前执行技能的模型（读取事实 + 实际看图）
        - 组合分析、素材分析、证据引用
                 |
                 v
   schema v2 结论校验（Python）
                 |
                 v
       静态最终 HTML + 可审计报告数据 JSON
```

### 确定性事实层

`prepare_diagnostic_analysis.py` 和 `diagnostic_analysis_lib.py` 继续读取 TOP 素材和每条 RPA 诊断记录。它们新增显式高光帧事实：每条帧包含素材 ID、`rankType`、`durationSec`、`metricValue`、规范化的本地图片路径和摘要警告。事实层只整理数据，不输出商业判断。

`diagnostic-analysis-data.json` 继续作为模型唯一的结构化数值和文本事实来源。高光帧文件由该 JSON 的路径索引，模型必须逐张打开实际 JPEG 后才能填写视觉观察。

### 模型分析层

`SKILL.md` 将把“外部分析环境”替换为同任务内必经步骤：批次验收通过、事实包已生成后，当前执行模型读取提示词和事实包、打开所有成功素材的高光帧、生成 `diagnostic-ai-analysis.json`，然后调用最终渲染器。

模型必须：

- 为每项人群结论标注具体指标层；不得用曝光占比推导成交优势。
- 对每条成功素材给出人群、脚本、高光帧三类分析；信息不足时明确写“数据不足以判断”。
- 只引用事实包内存在的人群分桶、脚本标签/原文证据和高光帧。
- 对高光帧写基于实际图片的画面观察；商业解释使用“与峰值同时出现”“可作为待验证假设”等谨慎措辞。
- 在 JSON 校验失败时修正结论，而不是继续渲染最终报告。

### 校验与报告层

`final_report_lib.py` 继续负责读取、校验、组装和渲染。它扩展为识别 schema v2，验证素材 ID、枚举值、证据引用和帧路径均属于当前运行事实。旧 schema v1 仍作为兼容输入，避免历史分析产物失效；新任务只生成 v2。

无 `analysis_json` 的显式草稿模式保留，但技能主流程不得使用它。只有 v2 校验通过，才能宣布本次报告分析完成。

## schema v2 结论契约

```json
{
  "schemaVersion": 2,
  "overallConclusion": "组合级商业结论",
  "batchInsights": [
    {
      "topic": "audience",
      "title": "洞察标题",
      "conclusion": "基于证据的谨慎结论",
      "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"]
    }
  ],
  "materialAnalyses": [
    {
      "materialId": "素材 ID",
      "audienceConclusion": "分层人群结论或数据不足说明",
      "scriptConclusion": "脚本结论或数据不足说明",
      "highlightAnalyses": [
        {
          "frameRef": "highlight:素材 ID:first_peak",
          "visualObservation": "来自实际 JPEG 的画面描述",
          "commercialInterpretation": "与峰值同时出现的谨慎解释"
        }
      ],
      "integratedConclusion": "三类证据整合后的判断",
      "decision": "scale",
      "nextAction": "具体行动",
      "evidenceRefs": ["..."]
    }
  ],
  "nextActions": [
    {"title": "行动标题", "body": "行动说明", "priority": "high"}
  ],
  "warnings": []
}
```

允许的 `topic` 为 `audience`、`script`、`highlight`、`integrated`；允许的 `decision` 为 `scale`、`remix`、`hold`、`stop`、`unknown`；优先级沿用 `high`、`medium`、`low`。`evidenceRefs` 的解析结果必须存在于事实包；引用不存在、指标不支持、素材不在当前批次或帧路径不匹配时校验失败。

## 任务流程与降级

1. 完成并验收 RPA、诊断记录和抽帧产物。
2. 生成包含高光帧索引的诊断事实包和模型提示词。
3. 当前模型读取事实包并实际查看 JPEG，写入 v2 结论 JSON。
4. 使用渲染器加载并校验结论 JSON；失败时回到第 3 步修正。
5. 渲染最终 HTML，并检查 `final-report-data.json` 状态。

批次验收失败、没有可分析素材或结论引用校验失败时，流程不得声称完成最终报告。若仅部分素材缺少诊断或帧，渲染器输出 `partial`，保留成功素材的分析，同时在 Tab 和数据质量区显示具体原因。

## 测试策略

- 事实包测试：覆盖高光帧索引、无摘要/无 JPEG 告警和路径规范化。
- schema 测试：覆盖 v2 合法输入、非法素材 ID、错误指标层、无效脚本引用、缺失帧引用和 v1 向后兼容。
- 报告数据测试：验证组合与素材两层分析被合并，三层人群不混淆，部分失败保持 `partial`。
- HTML 测试：验证移除组合总览、存在七个新章节、默认素材 Tab、所有 Tab/tabpanel 的可访问属性和静态切换脚本。
- 技能契约测试：验证主流程要求当前模型实际查看 JPEG、写 v2 结论并传入 `--analysis-json`；不得将草稿模式描述为完成。
- 回归测试：运行全量 `unittest`、`py_compile`，再以临时批次构造高光帧、事实包、v2 结论和最终 HTML 的端到端验证。

## 修改范围

- `SKILL.md`
- `prompts/diagnostic-analysis-prompt-v1.md`（或升级为 v2 模板）
- `scripts/prepare_diagnostic_analysis.py`
- `scripts/diagnostic_analysis_lib.py`
- `scripts/final_report_lib.py`
- `tests/test_diagnostic_analysis.py`
- `tests/test_final_report.py`
- `tests/test_skill_contract.py`

不修改 RPA 与峰值提取脚本，除非高光帧事实索引无法从现有 `summary.json` 稳定构建。
