# diagnostic-analysis-prompt-v1

当前执行技能的当前对话模型必须先读取 `analysis-units/analysis-unit-manifest.json`，校验其中五个章节事实包的路径、字节数、SHA-256、素材顺序和帧数量，再在一次模型调用中统一读取：

`report-display-facts.json` 是前端展示快照，不属于模型输入；模型不得读取、引用或复述其中的 TOP3/图表展示字段。

- `analysis-units/chapter01-portfolio-input.json`
- `analysis-units/chapter02-audience-input.json`
- `analysis-units/chapter03-script-input.json`
- `analysis-units/chapter04-highlight-input.json`
- `analysis-units/chapter05-material-input.json`

历史运行中的 `diagnostic-analysis-data.json` 仅作兼容留档；新运行不再生成，新分析不得读取它来补充或覆盖五个章节事实包。当前对话模型必须逐张实际查看 `chapter04-highlight-input.json` 中 `materials[*].frames[*].framePath` 指向的 JPEG，一次输出完整结果；不得拆成五次模型调用，不得使用外部模型服务、`codex exec` 或子进程替代当前对话模型。

只分析五个章节事实包提供的业务事实。不得分析执行过程，不得把运行目录名、任务上下文、代码修复、`verify` 收口、`fresh rerun`、`rpa_request_failed`、候选失败、补位、取消、重跑或批次调度写入任何输出字段。素材 rank 只表示当前已提取素材在输入中的排序；rank 缺口不得被解释为补位或失败。

五个模型事实包不包含平台临时签名视频或封面 URL。不得从兼容快照或其他运行产物补充、引用、复述、评价或建议使用这些 URL。

将结果写入单独的 `diagnostic-ai-analysis.json`。只输出合法 JSON，不要用 Markdown 代码块包裹。

## 五章分析边界

### Chapter 01：素材榜单数据洞察

<!-- 兼容历史契约：01｜TOP 素材组合结论；正式标题与输出均以“素材榜单数据洞察”为准。 -->
<!-- Legacy heading alias: ### Chapter 01：TOP 素材组合结论 -->
<!-- 历史契约短语保留：本轮不新增经营指标；当前版本已将新增指标限定在素材榜单事实包内。 -->
<!-- 历史兼容措辞：不得复用第 01 章人群组合结论；不得为了填充 TOP 组合结论而输出 integrated。 -->

只读取 `chapter01-portfolio-input.json` 的 `portfolio`，以其中的全量素材列表、近 7 天上新数量和榜单指标为事实，输出 `overallConclusion` 与唯一的 `portfolioDimensionConclusions.commercial`。`portfolio.materials` 是事实包提供的全量素材数据，用于事实核验，不是 RPA 选中素材清单；不得读取 Chapter 01 顶层 `materials` 或 `materialIds`，也不得从其他章节补充素材。近 7 天遵循 T+1 口径：以技能执行日 T 为基准，统计 `T-7` 至 `T-1`（含首尾）且 `material_create_time_v2` 落在该区间的素材。`summaryMetrics` 只保留当前 ROI 字段 `roi` 及消耗、点击率、转化率、3 秒完播率和 CPM；`summaryMetrics.roi` 来源字段为 `total_prepay_and_pay_order_roi2`，Chapter 01 事实包不包含 `netSettlementRoi` 或 `overallPaymentRoi`。禁止引用 Chapter 02～05 的人群、脚本、高光画面或互动时序事实，也不得从素材名称反推业务结论。`overallConclusion` 是第一部分唯一直接展示的叙事段落：建议控制在 180-260 个中文字符，在关键数据锚点之外至少包含三项分析判断（例如指标组合反映的效率关系、上新节奏风险、存量分层机会、归因边界或验证优先级），并明确分阶段动作，不能只是指标清单。`commercial.conclusion` 作为结构化备用结论，commercial.conclusion 不得重复 overallConclusion 中相同的指标串，应补充互不重复的业务含义和验证动作。

### Chapter 02：人群洞察

只以 `chapter02-audience-input.json` 的 `SHOW_COUNT`、`CLICK_COUNT`、`TRANSACTION_AMOUNT` 三种口径及其全部可用维度生成 `batchInsights[topic=audience]`。三条洞察的事实、判断和建议必须按口径隔离，不混用证据引用。`preferencesByMetric` 仅供报告偏好 drill-down 使用，AI 不读取，也不得将其中的 TOP3 素材字段写入人群洞察。

### Chapter 03：脚本洞察

只以 `chapter03-script-input.json` 的完整脚本、一二级标签、真实证据句、标签汇总和确定性 `keywordCloud` 生成 `batchInsights[topic=script]`、`keywordCloudReview` 及 `keywordCloudInsight`。`keywordCloudReview` 只能对 NLP 候选词做 `accept`、`exclude` 或 `merge` 审查，不得修改原始频次；后端会依据审查结果重新汇总。不要将脚本句子与高光帧秒点做无依据对齐；关键词原始频次不得由 AI 改写。

### Chapter 04：高光画面洞察

只以 `chapter04-highlight-input.json` 和实际查看的 JPEG 生成 `highlightDashboard`、`materialAnalyses[*].highlightAnalyses` 和 `frameCoverageNote`。图像结论不得由脚本文本、文件名或兼容快照代替。
每个视觉分类的 `commonConclusion` 必须输出 3 个 `{title,body,evidenceRefs}` 分点，`derivativeDirections` 也必须输出 3 个同序 `{title,body,evidenceRefs}` 分点；两组按索引一一对应，分别覆盖“画面主体与构图、信息层与动作机制、可迁移机会”，正文要给出可执行的验证动作和指标，不能只写短句。

### Chapter 05：单素材深度分析

以 `chapter05-material-input.json` 建立素材顺序、人群偏好和证据索引，再按同一 `materialId` 读取 Chapter 03 的脚本事实和 Chapter 04 的高光帧。输出每条素材的 `materialAnalyses`；不从兼容快照补充 `delivery`，不虚构五包未提供的单素材秒级走势或经营指标。

## 生成前门禁自检

图片查看是独立的工具阶段。若图片理解工具首次返回 `GUARDRAIL_ERROR` 或策略拒绝，立即暂停并标记 `frame_review_pending`：保留已查看和未查看的 `framePath` 清单，禁止继续拆分、逐帧重试或改用其他工具绕过拒绝；工具恢复后从未查看批次续接。

在写入 JSON 前先完成一次契约自检，不要边生成边试错：

- 读取 `scripts/final_report_lib.py` 中的 `ALLOWED_DECISIONS`、`AI_QUALITY_MINIMUMS` 和 `HIGHLIGHT_DASHBOARD_MINIMUMS`，先建立合法 `frameRef/evidenceRefs` 白名单及字段字符数预算，再严格按其中的枚举与字符数范围构造字段；decision 只能使用 `scale`、`remix`、`hold`、`stop` 或 `unknown`。
- 从 manifest 和五个章节事实包核对素材数量、每条素材的成功帧数量和 `missingFrames`；每张成功高光帧恰好一条 `highlightAnalyses`，dashboard 对每张成功帧恰好归类一次。
- 按素材分片查看画面，每片最多 6 张，先在内存中完成分片观察与对应结论，再合并为单个完整 JSON；确认所有 `frameRef`、`evidenceRefs` 都逐字来自对应章节事实包，确认 dashboard 只输出实际存在且非空的分类。写入后必须先执行 `python scripts/run_standard_analysis.py validate-analysis --output-dir "$PWD/runs/<run-id>" --analysis-json "$PWD/runs/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"`，校验通过前不要调用 `finalize`；校验失败时只修正报错字段并重新校验。禁止先写简短草稿、再以多轮全文补丁补足字符数或修正证据。

## 证据与归因规则

- 不得虚构事实，不得将相关性或峰值同时性表述为因果关系。
- 所有 AI 二次分析字段都必须采用“少量关键事实锚点 + 判断 + 建议”的写法：只选最能支撑判断的原始数值、占比、出现次数、素材数量或高光帧秒点作为依据，说明其业务含义和下一步如何验证；不得只写抽象结论，也不得把事实清单当作结论主体。
- 人群文案只能使用“展现次数”“点击次数”“成交金额”三个正式口径名称；不得用“触达”替代展现次数，不得用“响应”替代点击次数，也不得混写三种口径。
- `batchInsights[topic=audience]` 仅服务第 02 章人群洞察：必须恰好输出三条，依次覆盖 `chapter02-audience-input.json` 的展现次数（`SHOW_COUNT`）、点击次数（`CLICK_COUNT`）、成交金额（`TRANSACTION_AMOUNT`）。每条的标题不是固定文案，必须由 AI 基于对应口径的八大人群、年龄、性别和地域事实生成；单条的全部 `evidenceRefs` 只能属于一个对应的 `audience:{metric}:*` 口径。不得复用第 01 章素材榜单结论的标题、判断或建议。
- `portfolioDimensionConclusions` 只允许输出 `commercial` 一个 key，以兼容既有 schema；展示文案统一称为“素材榜单数据”。该结论必须综合近 7 天上新素材量、榜单消耗、ROI、点击率、转化率、3 秒完播率和 CPM，形成“事实锚点 → 榜单判断 → 下一步验证”。
- `commercial` 只能解释 `chapter01-portfolio-input.json` 的 `portfolio` 内已有指标；`portfolio.materials` 仅用于核验全量素材事实，不得把素材明细逐条展开成报告段落。指标名称、时间窗口和数值必须遵循事实包，不得自行改名或补造缺失指标；不得出现“资产表”或任何数据来源名称。其结论应优先写指标之间的关系、经营含义和下一轮验证设计，避免再次罗列 `overallConclusion` 已写出的完整指标串。
- 第 01 章不得输出人群、脚本、高光画面或互动时序分析；这些内容只允许出现在对应章节。
- `highlight` 只能使用 `chapter04-highlight-input.json` 中最终 TOP 素材的 `highlight:*` 证据，高光帧的结论必须是对组合画面机制的谨慎判断。
- 视觉分类必须只根据实际 JPEG 的主要视觉主体判断：`portrait` 为人物占据主要视觉焦点、产品仅为陪体；`product` 为包装、瓶身、内容物、开箱、食用或操作动作占据主要焦点，即使有人手或人物也优先归入 `product`；`other` 为无清晰人物/产品主体或焦点无法可靠判断。人物与产品同框时依据画面占比、视觉焦点和主体可辨识度，无法区分时归 `other`。严禁根据素材标题、文件名、脚本、品牌或标签猜分类。
- 对每条有高光帧的素材，必须分别填写人群、脚本和高光帧分析。事实不足时明确写“数据不足以判断”。
- 每一张高光帧都必须对应一条 `highlightAnalyses`；这里的高光帧仅指 `chapter04-highlight-input.json` 的 `materials[*].frames` 中成功生成的 JPEG。`highlightAnalyses` 数量必须等于该素材 `frames` 数量，`highlightAnalyses[*].frameRef` 集合必须与该素材 `frames[*].evidenceRef` 集合完全一致。不得只分析 zero_second 和 peak_top_1；若事实包包含 peak_top_2 至 peak_top_5，也必须分别输出对应分析。
- 若该素材在 `chapter04-highlight-input.json` 的 `materials[*].missingFrames` 非空，`frameCoverageNote` 必须说明哪个预期帧不可用，并明确视觉结论基于其余成功帧；`missingFrames` 为空时该字段必须是空字符串。少于 5 个已识别局部峰值属于点击曲线特征，不得写成技术缺失。
- 高光帧的 `visualObservation` 必须来自实际查看 JPEG 的画面观察；`commercialInterpretation` 使用“与点击峰值同时出现”“待验证假设”等谨慎表述。
- `evidenceRefs` 与 `frameRef` 必须逐字复制事实包中的证据标识，不得自造素材 ID、指标、分桶、脚本标签或帧类型。当前事实包没有脚本分段时码，不得宣称帧对应具体话术，也不得根据整段脚本猜测画面所在秒的话术。
- 事实包缺失输入时，不要生成单独的数据质量段落；必须在对应的 `batchInsights` 或 `materialAnalyses` 字段内用“数据不足以判断”等业务可见措辞说明边界。

## 内容长度与信息密度（标准入口 finalize 的硬性门禁）

- `overallConclusion` 建议为 180-260 个中文字符，概括素材上新规模、榜单核心指标、指标关系、至少三项分析判断、分阶段动作和验证边界；若事实包指标齐全，必须覆盖上新量、消耗、ROI、点击率、转化率、3 秒完播率和 CPM。`portfolioDimensionConclusions.commercial.conclusion` 必须为 100-180 个中文字符，至少覆盖上新量、消耗、ROI、点击率、转化率、3 秒完播率和 CPM 中可用的关键指标，并填写至少一个合法 `commercial:*` 证据引用；其文字不应重复 overallConclusion 的指标串。
- 每条 `batchInsights.conclusion` 必须为 180-280 个中文字符，包含具体指标名称和至少 2 个可核验事实点；事实点之后必须给出分析含义、投放判断和可验证假设，并填写至少一个合法 `evidenceRefs`。
- 每条 `materialAnalyses[*].audienceConclusion`、`scriptConclusion` 和 `integratedConclusion` 必须为 120-220 个中文字符；`nextAction` 至少 100 个中文字符，并填写至少一个合法 `evidenceRefs`。单素材 AI 二次总结应回答“这条素材为什么这样表现、应该保留什么、应该改写什么”，不要只复述页面已展示的秒级互动、脚本标签或证据句；不同素材不得复制相同结论或行动。
- 每条 `highlightAnalyses[*].visualObservation` 必须包含至少 40 个中文字符的具体画面观察；`commercialInterpretation` 必须为 80-140 个中文字符，把画面观察转化为谨慎的商业解释，例如理解成本、信任感、产品利益点、场景代入或行动触发，而不是再次描述画面元素。
- 必须输出至少 3 条 audience、2 条 script 洞察；`audience` 洞察需覆盖展现次数、点击次数、成交金额三个角度，事实不足时仍需给出下一步补充或验证方向。不得为了填充素材榜单数据洞察而输出 `batchInsights.topic=integrated`。
- `keywordCloudInsight` 必须包含 `oneSentenceConclusion`、`summary` 和 `sellingPoints`；卖点最多 4 条。每条卖点必须引用 `keywordCloud.keywords[*].keyword` 和合法 `evidenceRefs`，不得引入关键词云与脚本文本之外的新事实；前端展示顺序固定为 AI 二次分析、关键词云模块、脚本汇总数据。
- `script` 洞察固定输出 2 条且按以下顺序：第一条标题以“`一二级内容公式：`”开头，分析一级/二级标签的高频结构、组合方式和差异；第二条标题以“`脚本文案：`”开头，分析真实文案中的利益点、表达方式与证据句。两条都必须引用对应的 `script:*` 证据，且不能只复述标签或原句。

## 页面结构映射

最终 HTML 页面保留以下 5 个 section，输出字段必须围绕这些 section 收缩：

- `01｜素材榜单数据洞察`：标题为“素材榜单数据洞察”。前端只直接展示 `overallConclusion` 单段文字；`portfolioDimensionConclusions.commercial` 仅作为结构化结论、证据和兼容字段，不另起可见段落。不添加“从素材列表数据来看：”或其他编号式引导，不展示人群、脚本、高光画面、互动时序 point。
- `02｜人群洞察`：使用 `batchInsights` 中 `topic` 为 `audience` 的洞察。
- `03｜脚本洞察`：使用 `batchInsights` 中 `topic` 为 `script` 的洞察。
- `04｜高光画面洞察`：使用 `highlightDashboard`。按 `portrait`、`product`、`other` 的固定顺序输出实际存在的分类；每个成功帧只归入一个分类。`portrait` 是人物主视觉，`product` 是产品或包装主视觉，无法确定或两者都不是主视觉时归入 `other`。每组使用已查看 JPEG 的共同可见信号写 3 个分点式 `commonConclusion`，并按相同顺序给出 3 个正文不少于 20 个字符的可验证 `derivativeDirections`；不得把峰值同时性写成因果。
- `05｜单素材深度分析`：页面会先展示确定性的 `互动时序分析` 和 `脚本分析数据`，再展示 `materialAnalyses` 中的 AI 二次总结。AI 二次总结不要逐项复述页面已经展示的秒级互动数据、脚本标签和证据句，而要解释这些事实为什么影响素材放量、改写或保留判断。

不得输出 `batchInsights.topic=highlight`；不得输出 `batchInsights.topic=integrated`；高光帧不得作为第 01 章证据。

不得输出“数据质量与执行口径”“方法说明”“补充说明”等额外页面段落，也不得输出顶层 `warnings` 字段。

## 输出契约

输出对象必须满足：

```json
{
  "schemaVersion": 5,
  "portfolioDimensionConclusions": {
    "commercial": {
      "conclusion": "基于近 7 天上新量、榜单消耗、ROI、点击率、转化率、3 秒完播率和 CPM 的素材榜单判断",
      "evidenceRefs": ["commercial:newMaterialCount7d", "commercial:ctr", "commercial:cpm"]
    }
  },
  "highlightDashboard": {
    "groups": [
      {
        "category": "portrait",
        "frameRefs": ["highlight:事实包中的素材 ID:帧类型"],
        "commonConclusion": [
          {"title": "画面主体与构图", "body": "基于已查看 JPEG 的共同画面观察，不宣称峰值因果。", "evidenceRefs": ["highlight:事实包中的素材 ID:帧类型"]},
          {"title": "信息层与动作机制", "body": "说明信息层、动作和视觉焦点如何降低理解成本，并给出待验证指标。", "evidenceRefs": ["highlight:事实包中的素材 ID:帧类型"]},
          {"title": "可迁移机会", "body": "说明下一轮可复用或改写的画面变量，以及对应的对照验证方式。", "evidenceRefs": ["highlight:事实包中的素材 ID:帧类型"]}
        ],
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
  "keywordCloudInsight": {
    "oneSentenceConclusion": "基于脚本高频词给出的可读结论，必须引用事实中的场景、商品、优惠或动作表达。",
    "summary": "对高频关键词组合关系的总结，不改写关键词频次。",
    "sellingPoints": [
      {
        "title": "卖点标题",
        "description": "仅基于脚本文本和关键词云提炼的卖点描述。",
        "keywordRefs": ["关键词云中的真实词"],
        "evidenceRefs": ["script:materialId:text:关键词"]
      }
    ]
  },
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
