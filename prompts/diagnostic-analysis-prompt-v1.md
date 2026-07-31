# diagnostic-analysis-prompt-v1

当前执行技能的模型必须使用 `diagnostic-analysis-data.json` 作为唯一的结构化事实来源，并逐张实际查看 `highlights.materials[*].frames[*].framePath` 指向的 JPEG。

只分析 RPA 提取到的业务事实：人群、脚本、投放明细和高光帧。不得分析执行过程，不得把运行目录名、任务上下文、代码修复、`verify` 收口、`fresh rerun`、`rpa_request_failed`、候选失败、补位、取消、重跑或批次调度写入任何输出字段。素材 rank 只表示当前已提取素材在输入中的排序；rank 缺口不得被解释为补位或失败。

`materials[*].video` 仅用于最终报告保留素材来源元数据；其中的 `src` 是平台临时签名 URL。不得在任何分析字段中引用、复述、评价或建议使用该 URL。

将结果写入单独的 `diagnostic-ai-analysis.json`。只输出合法 JSON，不要用 Markdown 代码块包裹。

## 证据与归因规则

- 不得虚构事实，不得将相关性或峰值同时性表述为因果关系。
- 所有 AI 二次分析字段都必须采用“少量关键事实锚点 + 多句分析判断”的写法：只选最能支撑判断的原始数值、占比、出现次数、素材数量或高光帧秒点作为依据，然后展开说明这些事实意味着什么、为什么值得放量或改写、下一步如何验证；不得只写抽象结论，也不得把事实清单当作结论主体。
- 人群结论必须分别标注 `SHOW_COUNT`、`CLICK_COUNT` 或 `TRANSACTION_AMOUNT`，展示文案使用“展现次数”“点击次数”“成交金额”；不得将展现、点击和成交混写。
- 人群洞察必须分别从展现次数、点击次数、成交金额三个角度展开，至少引用每个角度的一条合法 `audience:*` 证据。
- `overallConclusion` 必须综合人群、脚本、高光帧和投放明细中的主要事实，对本次提取到的所有素材做组合级汇总分析，不得只复述单一人群或单一脚本结论。整体结论的数据陈述只作为判断依据，不作为段落主体；优先输出组合公式、机会判断、风险边界和下一轮验证方向。
- 换言之，overallConclusion 必须综合人群、脚本、高光帧，不得和人群洞察、脚本洞察或单素材分析重复或冲突。
- `01｜TOP 素材组合结论` 只使用 `overallConclusion`，不得再生成下方组合洞察卡片；不得输出 `batchInsights.topic=integrated`。
- 对每条有高光帧的素材，必须分别填写人群、脚本和高光帧分析。事实不足时明确写“数据不足以判断”。
- 每一张高光帧都必须对应一条 `highlightAnalyses`；`highlightAnalyses` 数量必须等于该素材 `highlights.materials[*].frames` 数量，`highlightAnalyses[*].frameRef` 集合必须与该素材 `frames[*].evidenceRef` 集合完全一致。不得只分析 zero_second 和 peak_top_1；若事实包包含 peak_top_2 或 peak_top_3，也必须分别输出对应分析。缺失任何一帧都不能视为完成。
- 高光帧的 `visualObservation` 必须来自实际查看 JPEG 的画面观察；`commercialInterpretation` 使用“与点击峰值同时出现”“待验证假设”等谨慎表述。
- `evidenceRefs` 与 `frameRef` 必须逐字复制事实包中的证据标识，不得自造素材 ID、指标、分桶、脚本标签或帧类型。
- 事实包缺失输入时，不要生成单独的数据质量段落；必须在对应的 `batchInsights` 或 `materialAnalyses` 字段内用“数据不足以判断”等业务可见措辞说明边界。

## 内容长度与信息密度

- `overallConclusion` 建议 420-680 个中文字符，最多用前 1/3 篇幅交代关键事实，其余篇幅用于解释组合表现、可复用机制、创意风险和下一轮策略判断。
- 每条 batchInsights.conclusion 建议 180-280 个中文字符，必须包含具体指标名称和至少 2 个可核验事实点，但事实点之后必须给出分析含义、投放判断和可验证假设。
- 每条 `materialAnalyses[*].audienceConclusion`、`scriptConclusion` 和 `integratedConclusion` 建议 120-220 个中文字符。单素材 AI 二次总结应回答“这条素材为什么这样表现、应该保留什么、应该改写什么”，不要只复述页面已展示的秒级互动、脚本标签或证据句。
- `highlightAnalyses[*].commercialInterpretation` 建议 80-140 个中文字符，必须把画面观察转化为谨慎的商业解释，例如理解成本、信任感、产品利益点、场景代入或行动触发，而不是再次描述画面元素。
- `nextActions[*].body` 建议 140-220 个中文字符，必须说明行动依据来自哪类数据、要验证的假设、执行方向和成功/失败后的判断口径；`priority` 只用于排序，最终页面策略卡片不展示 priority。
- 至少输出 3 条 audience、2 条 script 洞察。`audience` 洞察需覆盖展现次数、点击次数、成交金额三个角度；事实不足时可用“数据不足以判断”说明边界，但仍要给出下一步补充或验证方向。不得为了填充 TOP 组合结论而输出 `batchInsights.topic=integrated`。
- 至少输出 4 条 nextActions，覆盖放量、改写、验证、风险控制或素材复用中的不同动作，不要输出同义重复策略。

## 页面结构映射

最终 HTML 页面保留以下 5 个 section，输出字段必须围绕这些 section 收缩：

- `01｜TOP 素材组合结论`：只使用 `overallConclusion`，不展示、不生成下方组合洞察卡片。
- `02｜人群洞察`：使用 `batchInsights` 中 `topic` 为 `audience` 的洞察。
- `03｜脚本洞察`：使用 `batchInsights` 中 `topic` 为 `script` 的洞察。
- `04｜单素材深度分析`：页面会先展示确定性的 `互动时序分析` 和 `脚本分析数据`，再展示 `materialAnalyses` 中的 AI 二次总结。AI 二次总结不要逐项复述页面已经展示的秒级互动数据、脚本标签和证据句，而要解释这些事实为什么影响素材放量、改写或保留判断。
- `nextActions` 对应 `05｜下一轮策略`：策略卡片只展示 title 和 body，不展示 priority。

不得输出 `batchInsights.topic=highlight`；不得输出 `batchInsights.topic=integrated`；高光帧只允许作为 `overallConclusion` 和 `materialAnalyses[*].highlightAnalyses` 的证据来源，不再生成单独的批量高光帧或组合洞察段落。

不得输出“数据质量与执行口径”“方法说明”“补充说明”等额外页面段落，也不得输出顶层 `warnings` 字段。

## 输出契约

输出对象必须满足：

```json
{
  "schemaVersion": 2,
  "overallConclusion": "组合级商业结论",
  "batchInsights": [
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
