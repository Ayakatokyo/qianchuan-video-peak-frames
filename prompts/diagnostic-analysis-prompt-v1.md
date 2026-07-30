# diagnostic-analysis-prompt-v1

当前执行技能的模型必须使用 `diagnostic-analysis-data.json` 作为唯一的结构化事实来源，并逐张实际查看 `highlights.materials[*].frames[*].framePath` 指向的 JPEG。

只分析 RPA 提取到的业务事实：人群、脚本、投放明细和高光帧。不得分析执行过程，不得把运行目录名、任务上下文、代码修复、`verify` 收口、`fresh rerun`、`rpa_request_failed`、候选失败、补位、取消、重跑或批次调度写入任何输出字段。素材 rank 只表示当前已提取素材在输入中的排序；rank 缺口不得被解释为补位或失败。

`materials[*].video` 仅用于最终报告保留素材来源元数据；其中的 `src` 是平台临时签名 URL。不得在任何分析字段中引用、复述、评价或建议使用该 URL。

将结果写入单独的 `diagnostic-ai-analysis.json`。只输出合法 JSON，不要用 Markdown 代码块包裹。

## 证据与归因规则

- 不得虚构事实，不得将相关性或峰值同时性表述为因果关系。
- 所有 AI 二次分析字段都必须采用“真实数据 + 一句总结结论”的写法：先写事实包中的原始数值、占比、出现次数、素材数量或高光帧秒点，再给业务判断；不得只写抽象结论。
- 人群结论必须分别标注 `SHOW_COUNT`、`CLICK_COUNT` 或 `TRANSACTION_AMOUNT`，展示文案使用“展现次数”“点击次数”“成交金额”；不得将展现、点击和成交混写。
- 人群洞察必须分别从展现次数、点击次数、成交金额三个角度展开，至少引用每个角度的一条合法 `audience:*` 证据。
- `overallConclusion` 必须综合人群、脚本、高光帧和投放明细中的主要事实，对本次提取到的所有素材做组合级汇总分析，不得只复述单一人群或单一脚本结论。
- 换言之，overallConclusion 必须综合人群、脚本、高光帧，不得和下方任一单项洞察重复或冲突。
- `batchInsights.topic=integrated` 必须同时引用人群、脚本和高光帧证据；结论要说明这些证据如何组成可复用的 TOP 素材组合公式。
- 对每条有高光帧的素材，必须分别填写人群、脚本和高光帧分析。事实不足时明确写“数据不足以判断”。
- 每一张高光帧都必须对应一条 `highlightAnalyses`；`highlightAnalyses` 数量必须等于该素材 `highlights.materials[*].frames` 数量，`highlightAnalyses[*].frameRef` 集合必须与该素材 `frames[*].evidenceRef` 集合完全一致。不得只分析 zero_second 和 peak_top_1；若事实包包含 peak_top_2 或 peak_top_3，也必须分别输出对应分析。缺失任何一帧都不能视为完成。
- 高光帧的 `visualObservation` 必须来自实际查看 JPEG 的画面观察；`commercialInterpretation` 使用“与点击峰值同时出现”“待验证假设”等谨慎表述。
- `evidenceRefs` 与 `frameRef` 必须逐字复制事实包中的证据标识，不得自造素材 ID、指标、分桶、脚本标签或帧类型。
- 事实包缺失输入时，不要生成单独的数据质量段落；必须在对应的 `batchInsights` 或 `materialAnalyses` 字段内用“数据不足以判断”等业务可见措辞说明边界。

## 内容长度与信息密度

- 每条 batchInsights.conclusion 建议 120-220 个中文字符，必须包含具体指标名称和至少 2 个可核验事实点。
- `overallConclusion` 建议 260-420 个中文字符，必须覆盖素材数量、主力人群、脚本高频标签和高光帧共性。
- 每条 `materialAnalyses[*].audienceConclusion`、`scriptConclusion` 和 `integratedConclusion` 建议 80-160 个中文字符。
- `nextActions[*].body` 必须说明行动依据来自哪类数据，不要只写泛化建议；`priority` 只用于排序，最终页面策略卡片不展示 priority。

## 页面结构映射

最终 HTML 页面保留以下 5 个 section，输出字段必须围绕这些 section 收缩：

- `01｜TOP 素材组合结论`：使用 `overallConclusion` 和 `batchInsights` 中 `topic` 为 `integrated` 的洞察。
- `02｜人群洞察`：使用 `batchInsights` 中 `topic` 为 `audience` 的洞察。
- `03｜脚本洞察`：使用 `batchInsights` 中 `topic` 为 `script` 的洞察。
- `04｜单素材深度分析`：页面会先展示确定性的 `互动时序分析` 和 `脚本分析数据`，再展示 `materialAnalyses` 中的 AI 二次总结。AI 二次总结不要逐项复述页面已经展示的秒级互动数据、脚本标签和证据句，而要解释这些事实为什么影响素材放量、改写或保留判断。
- `nextActions` 对应 `05｜下一轮策略`：策略卡片只展示 title 和 body，不展示 priority。

不得输出 `batchInsights.topic=highlight`；高光帧只允许作为 `overallConclusion`、`batchInsights.topic=integrated` 和 `materialAnalyses[*].highlightAnalyses` 的证据来源，不再生成单独的批量高光帧洞察段落。

不得输出“数据质量与执行口径”“方法说明”“补充说明”等额外页面段落，也不得输出顶层 `warnings` 字段。

## 输出契约

输出对象必须满足：

```json
{
  "schemaVersion": 2,
  "overallConclusion": "组合级商业结论",
  "batchInsights": [
    {
      "topic": "integrated",
      "title": "组合洞察标题",
      "conclusion": "基于人群、脚本和高光帧证据的组合判断",
      "evidenceRefs": ["事实包中已有的引用"]
    },
    {
      "topic": "audience",
      "title": "洞察标题",
      "conclusion": "基于证据的谨慎结论",
      "evidenceRefs": ["事实包中已有的引用"]
    }
  ],
  "materialAnalyses": [
    {
      "materialId": "事实包中的素材 ID",
      "audienceConclusion": "人群分析或数据不足说明",
      "scriptConclusion": "脚本分析或数据不足说明",
      "highlightAnalyses": [
        {
          "frameRef": "逐字复制该素材 frames[*].evidenceRef；每张高光帧一条",
          "visualObservation": "基于该 JPEG 的画面观察",
          "commercialInterpretation": "谨慎的商业解释"
        }
      ],
      "integratedConclusion": "三类证据整合后的判断",
      "decision": "scale",
      "nextAction": "具体行动",
      "evidenceRefs": ["事实包中已有的引用"]
    }
  ],
  "nextActions": [
    {"title": "行动标题", "body": "行动说明", "priority": "high"}
  ]
}
```
