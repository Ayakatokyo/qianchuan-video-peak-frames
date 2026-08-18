# diagnostic-analysis-prompt-v1

当前执行技能的模型必须使用 `diagnostic-analysis-data.json` 作为唯一的结构化事实来源，并逐张实际查看 `highlights.materials[*].frames[*].framePath` 指向的 JPEG。

只分析 RPA 提取到的业务事实：人群、脚本、投放明细和高光帧。不得分析执行过程，不得把运行目录名、任务上下文、代码修复、`verify` 收口、`fresh rerun`、`rpa_request_failed`、候选失败、补位、取消、重跑或批次调度写入任何输出字段。素材 rank 只表示当前已提取素材在输入中的排序；rank 缺口不得被解释为补位或失败。

`materials[*].video` 仅用于最终报告保留素材来源元数据；其中的 `src` 是平台临时签名 URL。不得在任何分析字段中引用、复述、评价或建议使用该 URL。

将结果写入单独的 `diagnostic-ai-analysis.json`。只输出合法 JSON，不要用 Markdown 代码块包裹。

## 生成前门禁自检

在写入 JSON 前先完成一次契约自检，不要边生成边试错：

- 读取 `scripts/final_report_lib.py` 中的 `ALLOWED_DECISIONS`、`AI_QUALITY_MINIMUMS` 和 `HIGHLIGHT_DASHBOARD_MINIMUMS`，严格按其中的枚举与字符数范围构造字段；decision 只能使用 `scale`、`remix`、`hold`、`stop` 或 `unknown`。
- 从事实包核对素材数量、每条素材的成功帧数量和 `missingFrames`；每张成功高光帧恰好一条 `highlightAnalyses`，dashboard 对每张成功帧恰好归类一次。
- 确认所有 `frameRef`、`evidenceRefs` 都逐字来自事实包，确认 dashboard 只输出实际存在且非空的分类；自检未通过前不要写入 `diagnostic-ai-analysis.json`，也不要调用 `finalize`。

## 证据与归因规则

- 不得虚构事实，不得将相关性或峰值同时性表述为因果关系。
- 所有 AI 二次分析字段都必须采用“少量关键事实锚点 + 判断 + 建议”的写法：只选最能支撑判断的原始数值、占比、出现次数、素材数量或高光帧秒点作为依据，说明其业务含义和下一步如何验证；不得只写抽象结论，也不得把事实清单当作结论主体。
- 人群结论必须分别标注 `SHOW_COUNT`、`CLICK_COUNT` 或 `TRANSACTION_AMOUNT`，展示文案使用“展现次数”“点击次数”“成交金额”；不得将展现、点击和成交混写。
- 人群文案禁止使用“触达”替代展现次数，也禁止使用“响应”替代点击次数；必须逐字使用“展现次数”“点击次数”“成交金额”三个正式口径名称。
- 人群洞察必须分别从展现次数、点击次数、成交金额三个角度展开，至少引用每个角度的一条合法 `audience:*` 证据。
- 人群组合结论必须在展现次数、点击次数、成交金额三个口径下分别检查八大人群、年龄、性别和地域；不同口径不得混写。证据引用必须标明 `audience:{metric}:{dimension}:{bucket}`，只选事实包中实际存在的分桶。
- `portfolioDimensionConclusions` 必须分别输出素材列表、人群、脚本、高光画面和互动时序五条组合层结论。JSON key 仍使用 `commercial` 以兼容既有 schema，但展示文案必须称为“素材列表数据”。每条必须包含事实、判断和建议，且不得写成数据复述或长段分析；页面会将五条按固定顺序连续展示，不再使用 Tab。
- `commercial` 只能解释事实包 `commercial` 内的整体消耗、净成交金额、整体成交金额、净成交 ROI 和整体支付 ROI。不得自行计算、改名、替换指标或猜测缺失数值；不得出现“资产表”或任何数据来源名称。
- `audience` 与 `script` 是组合层判断，不能与 `batchInsights` 的对应洞察原文完全相同；不得输出 `batchInsights.topic=integrated`。
- `highlight` 只能使用事实包中最终 TOP 素材的 `highlight:*` 证据，高光帧的结论必须是对组合画面机制的谨慎判断。
- `interaction` 必须覆盖事实包中实际可用的整体点击次数、整体流失数、整体点赞次数、整体评论次数、整体转发次数和整体新增粉丝数，并结合相对播放位置分布判断互动发生阶段。整体总量引用 `interaction:{metric}:total`，区间分布引用 `interaction:{metric}:{bucket}`；总量和时序不能混写，不得把素材间相关性或峰值同时性表述为因果，不可用指标明确说明数据不足。
- 对每条有高光帧的素材，必须分别填写人群、脚本和高光帧分析。事实不足时明确写“数据不足以判断”。
- 每一张高光帧都必须对应一条 `highlightAnalyses`；这里的高光帧仅指事实包 `highlights.materials[*].frames` 中成功生成的 JPEG。`highlightAnalyses` 数量必须等于该素材 `frames` 数量，`highlightAnalyses[*].frameRef` 集合必须与该素材 `frames[*].evidenceRef` 集合完全一致。不得只分析 zero_second 和 peak_top_1；若事实包包含 peak_top_2 至 peak_top_5，也必须分别输出对应分析。
- 若该素材 `highlights.materials[*].missingFrames` 非空，`frameCoverageNote` 必须说明哪个预期帧不可用，并明确视觉结论基于其余成功帧；`missingFrames` 为空时该字段必须是空字符串。少于 5 个已识别局部峰值属于点击曲线特征，不得写成技术缺失。
- 高光帧的 `visualObservation` 必须来自实际查看 JPEG 的画面观察；`commercialInterpretation` 使用“与点击峰值同时出现”“待验证假设”等谨慎表述。
- `evidenceRefs` 与 `frameRef` 必须逐字复制事实包中的证据标识，不得自造素材 ID、指标、分桶、脚本标签或帧类型。当前事实包没有脚本分段时码，不得宣称帧对应具体话术，也不得根据整段脚本猜测画面所在秒的话术。
- 事实包缺失输入时，不要生成单独的数据质量段落；必须在对应的 `batchInsights` 或 `materialAnalyses` 字段内用“数据不足以判断”等业务可见措辞说明边界。

## 内容长度与信息密度（标准入口 finalize 的硬性门禁）

- `overallConclusion` 必须为 100-180 个中文字符，概括组合公式、最优先的后续动作和验证边界。每条 `portfolioDimensionConclusions[*].conclusion` 必须为 60-120 个中文字符，包含一个关键事实、一个判断和一个后续建议；每条必须填写至少一个对应维度的合法 `evidenceRefs`。经营表现只有在 `commercial.available=true` 时才可引用商业指标；不可用时明确说明数据不足以判断，不能补造数值。
- 每条 `batchInsights.conclusion` 必须为 180-280 个中文字符，包含具体指标名称和至少 2 个可核验事实点；事实点之后必须给出分析含义、投放判断和可验证假设，并填写至少一个合法 `evidenceRefs`。
- 每条 `materialAnalyses[*].audienceConclusion`、`scriptConclusion` 和 `integratedConclusion` 必须为 120-220 个中文字符；`nextAction` 至少 100 个中文字符，并填写至少一个合法 `evidenceRefs`。单素材 AI 二次总结应回答“这条素材为什么这样表现、应该保留什么、应该改写什么”，不要只复述页面已展示的秒级互动、脚本标签或证据句；不同素材不得复制相同结论或行动。
- 每条 `highlightAnalyses[*].visualObservation` 必须包含至少 40 个中文字符的具体画面观察；`commercialInterpretation` 必须为 80-140 个中文字符，把画面观察转化为谨慎的商业解释，例如理解成本、信任感、产品利益点、场景代入或行动触发，而不是再次描述画面元素。
- 必须输出至少 3 条 audience、2 条 script 洞察；`audience` 洞察需覆盖展现次数、点击次数、成交金额三个角度，事实不足时仍需给出下一步补充或验证方向。不得为了填充 TOP 组合结论而输出 `batchInsights.topic=integrated`。
- `script` 洞察固定输出 2 条且按以下顺序：第一条标题以“`一二级内容公式：`”开头，分析一级/二级标签的高频结构、组合方式和差异；第二条标题以“`脚本文案：`”开头，分析真实文案中的利益点、表达方式与证据句。两条都必须引用对应的 `script:*` 证据，且不能只复述标签或原句。
- 第 01 章脚本组合结论也必须同时覆盖“一二级内容公式”和“脚本文案”：前者提出可复用的标签组合及验证动作，后者提出利益点、表达方式或行动指令的改写建议；两条都必须以事实包证据为依据。

## 页面结构映射

最终 HTML 页面保留以下 5 个 section，输出字段必须围绕这些 section 收缩：

- `01｜TOP 素材组合结论`：标题为“组合结论”。先展示 `overallConclusion`，再按素材列表、人群、脚本、高光画面、互动时序的顺序连续展示 `portfolioDimensionConclusions`。每个维度以“从{维度}数据来看：”加粗引导，其中首条固定为“从素材列表数据来看：”，不展示 Tab、维度卡片或 ROI 指标卡。
- `02｜人群洞察`：使用 `batchInsights` 中 `topic` 为 `audience` 的洞察。
- `03｜脚本洞察`：使用 `batchInsights` 中 `topic` 为 `script` 的洞察。
- `04｜高光画面洞察`：使用 `highlightDashboard`。按 `portrait`、`product`、`other` 的固定顺序输出实际存在的分类；每个成功帧只归入一个分类。`portrait` 是人物主视觉，`product` 是产品或包装主视觉，无法确定或两者都不是主视觉时归入 `other`。每组使用已查看 JPEG 的共同可见信号写至少 20 个字符的 `commonConclusion`，再给出至少一条正文不少于 20 个字符的可验证 `derivativeDirections`；不得把峰值同时性写成因果。
- `05｜单素材深度分析`：页面会先展示确定性的 `互动时序分析` 和 `脚本分析数据`，再展示 `materialAnalyses` 中的 AI 二次总结。AI 二次总结不要逐项复述页面已经展示的秒级互动数据、脚本标签和证据句，而要解释这些事实为什么影响素材放量、改写或保留判断。

不得输出 `batchInsights.topic=highlight`；不得输出 `batchInsights.topic=integrated`；高光帧只允许作为 `portfolioDimensionConclusions.highlight`、`materialAnalyses[*].highlightAnalyses` 和 `highlightDashboard` 的证据来源。

不得输出“数据质量与执行口径”“方法说明”“补充说明”等额外页面段落，也不得输出顶层 `warnings` 字段。

## 输出契约

输出对象必须满足：

```json
{
  "schemaVersion": 5,
  "portfolioDimensionConclusions": {
    "commercial": {
      "conclusion": "基于整体消耗、净成交金额、整体成交金额及两项 ROI 的经营判断",
      "evidenceRefs": ["commercial:netSettlementRoi", "commercial:overallPaymentRoi"]
    },
    "audience": {
      "conclusion": "组合层人群判断，不复制下方详细人群洞察",
      "evidenceRefs": ["audience:CLICK_COUNT:age:事实包中的分桶"]
    },
    "script": {
      "conclusion": "组合层脚本判断，不复制下方详细脚本洞察",
      "evidenceRefs": ["script:事实包中的素材 ID:一级标签:二级标签"]
    },
    "highlight": {
      "conclusion": "基于最终 TOP 素材高光帧的组合画面判断",
      "evidenceRefs": ["highlight:事实包中的素材 ID:帧类型"]
    },
    "interaction": {
      "conclusion": "基于跨素材相对播放位置聚合的互动时序判断",
      "evidenceRefs": ["interaction:CLICK_COUNT:0-10%"]
    }
  },
  "highlightDashboard": {
    "groups": [
      {
        "category": "portrait",
        "frameRefs": ["highlight:事实包中的素材 ID:帧类型"],
        "commonConclusion": "基于已查看 JPEG 的共同画面观察，不宣称峰值因果。",
        "derivativeDirections": [
          {
            "title": "待验证画面变量",
            "body": "说明保留或调整的画面变量以及下一轮如何验证。",
            "evidenceRefs": ["highlight:事实包中的素材 ID:帧类型"]
          }
        ]
      }
    ]
  },
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
      "frameCoverageNote": "仅当 missingFrames 非空时填写对应素材的缺失帧说明，否则为空字符串",
      "integratedConclusion": "三类证据整合后的判断",
      "decision": "scale",
      "nextAction": "具体行动",
      "evidenceRefs": ["事实包中已有的引用"]
    }
  ]
}
```
