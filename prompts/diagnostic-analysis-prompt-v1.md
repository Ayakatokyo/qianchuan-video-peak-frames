# diagnostic-analysis-prompt-v1

当前执行技能的模型必须使用 `diagnostic-analysis-data.json` 作为唯一的结构化事实来源，并逐张实际查看 `highlights.materials[*].frames[*].framePath` 指向的 JPEG。

只分析 RPA 提取到的业务事实：人群、脚本、投放明细和高光帧。不得分析执行过程，不得把运行目录名、任务上下文、代码修复、`verify` 收口、`fresh rerun`、`rpa_request_failed`、候选失败、补位、取消、重跑或批次调度写入任何商业结论、洞察、下一步或 warnings。素材 rank 只表示当前已提取素材在输入中的排序；rank 缺口不得被解释为补位或失败。

将结果写入单独的 `diagnostic-ai-analysis.json`。只输出合法 JSON，不要用 Markdown 代码块包裹。

## 证据与归因规则

- 不得虚构事实，不得将相关性或峰值同时性表述为因果关系。
- 人群结论必须分别标注 `SHOW_COUNT`、`CLICK_COUNT` 或 `TRANSACTION_AMOUNT`；不得将曝光、点击和成交混写。
- 对每条有高光帧的素材，必须分别填写人群、脚本和高光帧分析。事实不足时明确写“数据不足以判断”。
- 高光帧的 `visualObservation` 必须来自实际查看 JPEG 的画面观察；`commercialInterpretation` 使用“与点击峰值同时出现”“待验证假设”等谨慎表述。
- `evidenceRefs` 与 `frameRef` 必须逐字复制事实包中的证据标识，不得自造素材 ID、指标、分桶、脚本标签或帧类型。
- 事实包有 warnings 或缺失输入时，必须在 `warnings` 中说明不确定性。

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
          "frameRef": "事实包中已有的高光帧引用",
          "visualObservation": "基于 JPEG 的画面观察",
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
  ],
  "warnings": []
}
```
