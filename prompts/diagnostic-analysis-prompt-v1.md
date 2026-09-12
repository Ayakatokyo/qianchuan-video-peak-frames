# diagnostic-analysis-prompt-v1

<!-- highlight-depth-policy: mechanism-depth-v1 -->

当前执行技能的当前对话模型必须先读取 `analysis-units/analysis-unit-manifest.json`，校验其中五个章节事实包的路径、字节数、SHA-256、素材顺序和帧数量，再分批完整读取以下输入，允许当前模型分批撰写，最终合并为唯一正式 JSON：

`report-display-facts.json` 是前端展示快照，不属于模型输入；模型不得读取、引用或复述其中的 TOP3/图表展示字段。

- `analysis-units/chapter01-portfolio-input.json`
- `analysis-units/chapter02-audience-input.json`
- `analysis-units/chapter03-script-input.json`
- `analysis-units/chapter04-highlight-input.json`
- `analysis-units/chapter05-material-input.json`

历史运行中的 `diagnostic-analysis-data.json` 仅作兼容留档；新运行不再生成，新分析不得读取它来补充或覆盖五个章节事实包。当前对话模型必须逐张实际查看 `chapter04-highlight-input.json` 中 `materials[*].frames[*].framePath` 指向的 JPEG，完整覆盖最终结果；分析片段最终必须合并为同一完整结果；此前允许当前对话模型分批读取、查看图片及记录事实要点，不得使用外部模型服务、`codex exec` 或子进程替代当前对话模型。

只分析五个章节事实包提供的业务事实。不得分析执行过程，不得把运行目录名、任务上下文、代码修复、`verify` 收口、`fresh rerun`、`rpa_request_failed`、候选失败、补位、取消、重跑或批次调度写入任何输出字段。素材 rank 只表示当前已提取素材在输入中的排序；rank 缺口不得被解释为补位或失败。

五个模型事实包不包含平台临时签名视频或封面 URL。不得从兼容快照或其他运行产物补充、引用、复述、评价或建议使用这些 URL。

将结果写入单独的 `diagnostic-ai-analysis.json`。只输出合法 JSON，不要用 Markdown 代码块包裹。

## 接续、读取与真实重复画面

- 当前模型直接完成分析，不需要额外的模型调用接口。按章节和素材读取完整输入；响应截断时沿 JSON 路径继续，记录已读范围，不能把截断输出当作完整事实。
- 首次看图前使用正式 `frame-review` 命令冻结 `diagnostic-analysis/analysis-input-index.json`，逐项锁定 `frameRef`、`materialId`、`framePath` 和实际 SHA-256。路径、文件哈希、事实或 Prompt 改变时停止当前批次，显式重建索引后恢复，不混用新旧输入。
- 按素材处理，每批最多 6 个唯一 SHA；先完成命令返回的 `imagesToView` 扫描，再一次提交该批 checkpoint。相同 SHA 只实际查看一次，程序把模型提供的 `visualObservation` 缓存到 `visualObservationCache`，并维护全部 `frameRefsBySha256`；仍须覆盖每个 frameRef，不能跨素材复用商业解释。相邻画面变化依赖各自上下文，不作为共享缓存事实。操作结构见技能运维手册，不逐帧 patch、不借程序生成分析正文。
- `frame-review-progress.json` 必须在全部引用覆盖后为 `status=complete`、`pendingFrameRefs=[]` 且有 `completedAt`，否则先续接帧审阅，不能 finalize。质量门禁元数据只保存在机器产物，业务分析正文不复述结构校验、内容复核状态或清理日志。
- 只用本次 manifest 对应的事实与 JPEG 生成新结果。历史 AI JSON 不是 schema 模板；如需结构参考，使用下文契约，示例文字也不得复制为本批次事实。序列化工具可以保存模型逐项撰写的内容，不能批量拼接分析正文或补字数。
- “独有”“不同”均以图像实际信息为前提。真实相同的 JPEG 可有相同视觉观察；由本地文件计算 SHA-256 证明相同，不能用 AI 自报的哈希、路径或素材名代替。每个 frameRef 仍须单独覆盖。若相邻帧没有变化，明确没有可见变化；字幕不可辨或没有字幕时如实写出，不制造叙事转折。
- 相同图片不能证明视频、人群、互动或商业效果相同。commercialInterpretation 仍须核对本素材事实；不要求凭空制造不同商业机制，没有关联证据时说明边界。若相似度检查命中真实但相同的解释，保留候选结果及冲突帧证据，由当前模型按质量复核契约记录证据与理由，禁止随机标记、同义改写和关闭门禁。
- 字数和相似度是质量代理指标，不构成补造事实的授权。结构/引用错误按当前白名单修正；事实不足或疑似误判先定位具体输入和字段，不能反复用统一尾句填充。

## 五章分析边界

### Chapter 01：素材榜单数据洞察

<!-- 兼容历史契约：01｜TOP 素材组合结论；正式标题与输出均以“素材榜单数据洞察”为准。 -->
<!-- Legacy heading alias: ### Chapter 01：TOP 素材组合结论 -->
<!-- 历史契约短语保留：本轮不新增经营指标；当前版本已将新增指标限定在素材榜单事实包内。 -->
<!-- 历史兼容措辞：不得复用第 01 章人群组合结论；不得为了填充 TOP 组合结论而输出 integrated。 -->

只读取 `chapter01-portfolio-input.json` 的 `portfolio`，以其中的全量素材列表、近 7 天上新数量和榜单指标为事实，输出 `overallConclusion` 与唯一的 `portfolioDimensionConclusions.commercial`。`portfolio.materials` 是事实包提供的全量素材数据，用于事实核验，不是 RPA 选中素材清单；不得读取 Chapter 01 顶层 `materials` 或 `materialIds`，不得读取 `topMaterialRoi`，不得使用 RPA 精选素材数量、消耗，不得使用候选补位结果，不得从 Chapter 05 回退，也不得从其他章节补充素材。近 7 天遵循 T+1 口径：以技能执行日 T 为基准，统计 `T-7` 至 `T-1`（含首尾）且 `material_create_time_v2` 落在该区间的素材。`summaryMetrics` 只保留当前 ROI 字段 `roi` 及消耗、点击率、转化率、3 秒完播率和 CPM；`summaryMetrics.roi` 来源字段为 `total_prepay_and_pay_order_roi2`，Chapter 01 事实包不包含 `netSettlementRoi` 或 `overallPaymentRoi`。禁止引用 Chapter 02～05 的人群、脚本、高光画面或互动时序事实，也不得从素材名称反推业务结论。`overallConclusion` 是第一部分唯一直接展示的叙事段落：建议控制在 180-260 个中文字符，在关键数据锚点之外至少包含三项分析判断（例如指标组合反映的效率关系、上新节奏风险、存量分层机会、归因边界或验证优先级），并明确分阶段动作，不能只是指标清单。`commercial.conclusion` 作为结构化备用结论，commercial.conclusion 不得重复 overallConclusion 中相同的指标串，应补充互不重复的业务含义和验证动作。

若 `portfolio.hotMaterialRule` 存在，必须把 `thresholdCost`、`comparison`、`qualifyingCount` 和 `qualifyingMaterials[*]` 当作只读确定性事实；命中数量使用实际 `qualifyingCount`，素材名称、素材 ID、消耗、辅助指标和 `evidenceRef` 必须逐字引用，不得由 AI 重新计算、排序、改写、补充或删除。基于这些事实新增结构化 `hotMaterialInsight`：结论只解释爆款数量、消耗与效率是否匹配、保留/复刻与小预算对照验证建议，不把相关性写成因果关系；`evidenceRefs` 只能引用 `commercial:hotMaterialCount` 和事实包中实际存在的 `commercial:hotMaterial:<materialId>`。`hotMaterialInsight` 在 JSON 中保持独立，便于证据追溯；Presentation 会把它与 `overallConclusion` 合并为第一部分唯一的 AI 展示内容，爆款素材面板只展示确定性详情，不再重复展示 AI 卡片。若历史事实包没有 `hotMaterialRule`，不要猜测爆款，可以省略该字段；前端仍显示确定性面板或空状态。

### Chapter 02：人群洞察

只以 `chapter02-audience-input.json` 的 `SHOW_COUNT`、`CLICK_COUNT`、`TRANSACTION_AMOUNT` 三种口径及其全部可用维度生成 `batchInsights[topic=audience]`。三条洞察的事实、判断和建议必须按口径隔离，不混用证据引用。`preferencesByMetric` 仅供报告偏好 drill-down 使用，AI 不读取，也不得将其中的 TOP3 素材字段写入人群洞察。

### Chapter 03：脚本洞察

先审查 keywordCloud 候选词，保留有脚本证据的品类、品质/工艺、福利、场景、人群、产品状态或信任表达；排除口头填充词、纯动作词和不能独立支撑卖点的词。候选词频次不是卖点证据充分性的判断标准。

`keywordCloudInsight.analysisStatus` 必填：`supported` 时 `sellingPoints` 至少 3 条、至少覆盖 2 个真实不同的 `dimension`；每条必填 dimension、title、description、keywordRefs、evidenceRefs。维度名称由实际脚本决定。keywordRefs 逐字引用未排除的原始关键词，每个词至少有一条自身的 script evidenceRef 被该卖点引用；核对原句是否支持描述，不能把功效宣传当作已证实功效。不得同义改写一条卖点凑数。

不足以形成 3 条时，使用 `analysisStatus: "insufficient_evidence"`，填写 `limitation` 说明已读证据、可支持的维度及具体缺口；保留能成立的卖点，允许空数组但不得静默为空。程序不自动推断足够证据；该判断须由当前模型复核。

只以 `chapter03-script-input.json` 的完整脚本、一二级标签、真实证据句、标签汇总和确定性 `keywordCloud` 生成 `batchInsights[topic=script]`、`keywordCloudReview` 及 `keywordCloudInsight`。`keywordCloudReview` 只能对 NLP 候选词做 `accept`、`exclude` 或 `merge` 审查，不得修改原始频次；后端会依据审查结果重新汇总。不要将脚本句子与高光帧秒点做无依据对齐；关键词原始频次不得由 AI 改写。
`keywordCloudInsight.oneSentenceConclusion` 写约 28-45 个中文字符的单一核心判断，可补充关键词所反映的业务含义或承接关系；不得重复关键词频次、卖点卡片和验证动作，不要列举多个关键词或并列卖点。summary 控制在 60-110 个中文字符，适度补充关键词组合、品质/场景承接与验证方向；验证动作放入 summary 或 sellingPoints，不要塞进一句话结论。

### Chapter 04：高光画面洞察

只以 `chapter04-highlight-input.json` 和实际查看的 JPEG 生成 `highlightDashboard`、`materialAnalyses[*].highlightAnalyses` 和 `frameCoverageNote`。图像结论不得由脚本文本、文件名或兼容快照代替。

当前模型按素材查看图片，直接保存简短观察和分析 JSON 片段；固定合并程序只连接已经写好的数据。不得创建临时脚本或以内联代码循环、拼接、补长业务正文。每帧记录真实可见的主体、动作、产品/空间状态、信息层及相邻变化；无变化或字幕不可辨时如实说明，不强求六个角度全部有新信息。

本批次分类默认输出 **3–5 个机制类别**：先逐帧判断主导信息任务，再按可解释、互有区别的纳入标准归组。同帧含多种元素时选择主导机制，次要元素写入分析；不能因为视频同时包含人物、产品和操作，就把全部画面放进一个“混合机制”总类。不得按素材 ID、人物身份或标准分类简单拆组；人物情境、商品识别、质地/工艺呈现、食用操作、权益信息等只是核对候选，不是预设必须出现的类别。逐组 description 写清纳入条件及与相邻类别的边界，每个成功 frameRef 仍恰好覆盖一次。

highlightDashboard（标准分类）和 customClassification（本批次分类）的**每个非空类别**，commonConclusion 与 derivativeDirections **各输出 2–3 条**。这是正常交付要求，不再以各一条为默认合格结果。先找出本组 2–3 个可区分的信息作用、呈现方式或可测试问题，再逐条写清依据；一条里堆叠三个变量不能代替三条独立分析。同一帧可支持不同角度，但不能同义改写、重复正文或添加空泛尾句凑数。

每条共性结论包含：本组真实可见的共同信号、信息传递作用、推断边界。每条裂变方向包含：源画面、保留项、一个明确改变的画面变量、对照方式和观察指标；方向之间应测试不同问题。全部逐条绑定本组真实 evidenceRefs；结论和方向数量不要求机械一一对应，但每个方向都须有组内画面依据。

仅在实际证据不足时可少于上述数量：类别不足 3 个须填写 customClassification.classificationLimitation；某组的结论或方向不足 2 条须填写该组 contentLimitation，说明已核对的画面、能支持什么及无法继续细分/提出变量的具体缺口。程序会产生独立复核项，只有 evidence_limited 决定可放行，并在对应分类/分析区展示边界。缺少限制说明会硬失败；不能用“不能证明成交因果”替代对画面类别不足的说明，因为分类依据是可见机制而非因果证明。两类限制字段仅用于真实不足，不默认填写。共性结论仍至少保留一条有依据的观察或具体证据限制；无法支持任何裂变方向时可留空数组并说明原因。禁止以“保持主体、替换一个变量、比较 ROI”等通用模板代替分析。

逐帧 commercialInterpretation 有三种形式：默认 supported 写简短解释；insufficient_evidence 留空解释并填写具体 limitation；shared 留空解释并填写 sharedInterpretationRef，只能引用同素材、相同本地 JPEG 哈希的直接 supported 解释。不得跨素材、链式或循环共享；图片相同不能证明商业效果相同。

### Chapter 05：单素材深度分析

只依据 chapter05-material-input.json 的本素材人群、脚本、互动时序事实和已审 JPEG 生成 materialAnalyses；未知关联如实留空或说明不足。素材总结补充页面事实的业务含义，不重复整段事实列表。

## 生成前门禁自检

图片查看是独立的工具阶段。若图片理解工具首次返回 `GUARDRAIL_ERROR` 或策略拒绝，立即暂停并标记 `frame_review_pending`：保留已查看和未查看的 `framePath` 清单，禁止继续拆分、逐帧重试或改用其他工具绕过拒绝；工具恢复后从未查看批次续接。

在写入 JSON 前先完成一次契约自检，不要边生成边试错：

- 顶层填写 `analysisProvenance: {"author": "current_model", "businessTextGeneration": "direct"}`。这是可核查声明，不是来源的技术证明。临时脚本、循环、字符串模板改写过的正文不得仅补声明继续交付，须保留失败候选并重新审阅事实、撰写新候选。复核单只记录证据审阅，不能承载正文或替代缺失字段。
- Chapter 03 卖点语义和 Chapter 04 每个机制组始终需内容复核，不能通过改写文字逃避。supported 复核说明逐条证据与机制/变量的对应关系；evidence_limited 指出具体限制，交付摘要和 HTML 保留该限制。

- 读取 `scripts/final_report_lib.py` 中的 `ALLOWED_DECISIONS` 和 `AI_LENGTH_SUGGESTIONS`，先建立合法 frameRef/evidenceRefs 白名单。枚举和引用属于硬契约，字符数仅为表达建议，不能为凑字数生成正文；decision 只能使用 `scale`、`remix`、`hold`、`stop` 或 `unknown`。
- 从 manifest 和五个章节事实包核对素材数量、每条素材的成功帧数量和 `missingFrames`；每张成功高光帧恰好一条 `highlightAnalyses`，dashboard 对每张成功帧恰好归类一次。
- 按素材分片查看画面，每片最多 6 张，先在内存中完成分片观察与对应结论，再合并为单个完整 JSON；确认所有 `frameRef`、`evidenceRefs` 都逐字来自对应章节事实包，确认 dashboard 只输出实际存在且非空的分类。写入后必须先执行 `python scripts/run_standard_analysis.py validate-analysis --output-dir "$PWD/runs/<run-id>" --analysis-json "$PWD/runs/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"`，校验通过前不要调用 `finalize`；校验失败时只修正报错字段并重新校验。允许当前模型分片撰写和实质修正，不允许批量补字或替换标记求通过。

## 证据与归因规则

- 不得虚构事实，不得将相关性或峰值同时性表述为因果关系。
- 所有 AI 二次分析字段都必须采用“少量关键事实锚点 + 判断 + 建议”的写法：只选最能支撑判断的原始数值、占比、出现次数、素材数量或高光帧秒点作为依据，说明其业务含义和下一步如何验证；不得只写抽象结论，也不得把事实清单当作结论主体。
- 人群文案只能使用“展现次数”“点击次数”“成交金额”三个正式口径名称；不得用“触达”替代展现次数，不得用“响应”替代点击次数，也不得混写三种口径。
- `batchInsights[topic=audience]` 仅服务第 02 章人群洞察：必须恰好输出三条，依次覆盖 `chapter02-audience-input.json` 的展现次数（`SHOW_COUNT`）、点击次数（`CLICK_COUNT`）、成交金额（`TRANSACTION_AMOUNT`）。每条的标题不是固定文案，必须由 AI 基于对应口径的八大人群、年龄、性别和地域事实生成；单条的全部 `evidenceRefs` 只能属于一个对应的 `audience:{metric}:*` 口径。不得复用第 01 章素材榜单结论的标题、判断或建议。
- `portfolioDimensionConclusions` 只允许输出 `commercial` 一个 key，以兼容既有 schema；展示文案统一称为“素材榜单数据”。该结论必须综合近 7 天上新素材量、榜单消耗、ROI、点击率、转化率、3 秒完播率和 CPM，形成“事实锚点 → 榜单判断 → 下一步验证”。
- `hotMaterialInsight` 在数据结构上与整体经营结论分离，只解释 `portfolio.hotMaterialRule` 已命中的素材；阈值、命中数量、素材名称、素材指标和证据引用都是只读事实，不能重新计算、改写或虚构。报告展示时必须把它合并到现有 AI 二次分析区域，不能再渲染第二个同名 AI 卡片。
- `commercial` 只能解释 `chapter01-portfolio-input.json` 的 `portfolio` 内已有指标；`portfolio.materials` 仅用于核验全量素材事实，不得把素材明细逐条展开成报告段落。指标名称、时间窗口和数值必须遵循事实包，不得自行改名或补造缺失指标；不得出现“资产表”或任何数据来源名称。其结论应优先写指标之间的关系、经营含义和下一轮验证设计，避免再次罗列 `overallConclusion` 已写出的完整指标串。
- 第 01 章不得输出人群、脚本、高光画面或互动时序分析；这些内容只允许出现在对应章节。
- `highlight` 只能使用 `chapter04-highlight-input.json` 中最终 TOP 素材的 `highlight:*` 证据，高光帧的结论必须是对组合画面机制的谨慎判断。
- 视觉分类必须只根据实际 JPEG 的主要视觉主体判断：`portrait` 为人物占据主要视觉焦点、产品仅为陪体；`product` 为包装、瓶身、内容物、开箱、食用或操作动作占据主要焦点，即使有人手或人物也优先归入 `product`；`other` 为无清晰人物/产品主体或焦点无法可靠判断。人物与产品同框时依据画面占比、视觉焦点和主体可辨识度，无法区分时归 `other`。严禁根据素材标题、文件名、脚本、品牌或标签猜分类。
- 对每条有高光帧的素材，必须分别填写人群、脚本和高光帧分析。事实不足时明确写“数据不足以判断”。
- 每一张高光帧都必须对应一条 `highlightAnalyses`；这里的高光帧仅指 `chapter04-highlight-input.json` 的 `materials[*].frames` 中成功生成的 JPEG。`highlightAnalyses` 数量必须等于该素材 `frames` 数量，`highlightAnalyses[*].frameRef` 集合必须与该素材 `frames[*].evidenceRef` 集合完全一致。不得只分析 zero_second 和 peak_top_1；若事实包包含 peak_top_2 至 peak_top_5，也必须分别输出对应分析。
- 若该素材在 `chapter04-highlight-input.json` 的 `materials[*].missingFrames` 非空，`frameCoverageNote` 必须说明哪个预期帧不可用，并明确视觉结论基于其余成功帧；`missingFrames` 为空时该字段必须是空字符串。少于 5 个已识别局部峰值属于点击曲线特征，不得写成技术缺失。
- 高光帧的 `visualObservation` 必须来自实际 JPEG。商业解释仅依据可支持的画面机制与本素材事实，证据不足使用上述结构化状态。不同 JPEG 可能没有可见差异，不虚构独有动作、字幕和叙事作用。
- `evidenceRefs` 与 `frameRef` 必须逐字复制事实包中的证据标识，不得自造素材 ID、指标、分桶、脚本标签或帧类型。当前事实包没有脚本分段时码，不得宣称帧对应具体话术，也不得根据整段脚本猜测画面所在秒的话术。
- 事实包缺失输入时，不要生成单独的数据质量段落；必须在对应的 `batchInsights` 或 `materialAnalyses` 字段内用“数据不足以判断”等业务可见措辞说明边界。

## 信息密度与校验分层

正文只保留支持判断的事实、含义及必要的验证方向。长度与标题写法仅作表达建议，短而充分的文字可交付；不以最低字数要求扩写证据不足，也不强求每帧独立实验。

人群仍按展现次数、点击次数、成交金额三种口径分别输出，不混用证据。脚本仍保留“一二级内容公式：”和“脚本文案：”两个分析角度；缺少对应事实时在字段内明确具体限制，不创造引用。keywordCloudInsight 包含 oneSentenceConclusion、summary、sellingPoints，简明表达核心判断，不改写关键词原始频次。

validate-analysis 输出 error、review、advice 三层：error 必须修正；review 由当前模型回查对应全部事实/图片，支持合理重复或共同证据限制时写绑定候选与输入哈希的 quality-review.json；advice 不阻断交付。批量套话必须重写，不能通过复核记录放行。两个命令均传同一 --quality-review；候选或输入变化后旧复核失效。不得反复补长、换同义词、拆写素材 ID 或关闭门禁。

## 页面结构映射

最终 HTML 页面保留以下 5 个 section，输出字段必须围绕这些 section 收缩：

- `01｜素材榜单数据洞察`：标题为“素材榜单数据洞察”。前端只直接展示 `overallConclusion` 单段文字；`portfolioDimensionConclusions.commercial` 仅作为结构化结论、证据和兼容字段，不另起可见段落。不添加“从素材列表数据来看：”或其他编号式引导，不展示人群、脚本、高光画面、互动时序 point。
- `02｜人群洞察`：使用 `batchInsights` 中 `topic` 为 `audience` 的洞察。
- `03｜脚本洞察`：使用 `batchInsights` 中 `topic` 为 `script` 的洞察。
- `04｜高光画面洞察`：highlightDashboard 按 portrait、product、other 固定顺序输出实际非空分类，每帧恰好归类一次。customClassification 按真实主导机制输出 3–5 类，每组包含 groupId、语义化 groupName、description、frameRefs、2–3 条 commonConclusion 和 2–3 条 derivativeDirections；真实不足须按 Chapter 04 的 classificationLimitation / contentLimitation 契约声明并复核；分点包含 title、body、属于本组的 evidenceRefs。不能强求不同分类使用不同验证方法。ruleId、ruleName、ruleDescription、ruleVersion、inputFrameManifestHash 仍必填；未归类成功帧进入 unclassifiedFrameRefs，不得跨组重复。AI 不得输出或修正帧数、占比、覆盖率；这些统计由 Presentation 确定性计算。
- `05｜单素材深度分析`：每个工作区先展示 `materialAnalyses` 中对应的 AI 二次分析，再展示确定性的真实事实数据（包括互动时序分析和脚本分析数据）；高光帧观察保留“视频/高光帧在上、文字分析在下”的事实优先排版。AI 二次总结不要逐项复述页面已经展示的秒级互动数据、脚本标签和证据句，而要解释这些事实为什么影响素材放量、改写或保留判断。

不得输出 `batchInsights.topic=highlight`；不得输出 `batchInsights.topic=integrated`；高光帧不得作为第 01 章证据。

不得输出“数据质量与执行口径”“方法说明”“补充说明”等额外页面段落，也不得输出顶层 `warnings` 字段。

## 输出契约

以下仅演示字段形状和正常数量，不是可直接提交的本批次内容；机制甲/乙/丙、素材引用和正文均为占位，不是固定分类或业务事实，必须按实际 JPEG 重新撰写。片段合并后仍须覆盖全部素材、成功帧和规定分析口径。输出对象必须满足：

```json
{
  "schemaVersion": 5,
  "analysisProvenance": {"author": "current_model", "businessTextGeneration": "direct"},
  "overallConclusion": "基于本批次榜单事实的判断或具体证据限制",
  "portfolioDimensionConclusions": {
    "commercial": {
      "conclusion": "基于近 7 天上新量、榜单消耗、ROI、点击率、转化率、3 秒完播率和 CPM 的素材榜单判断",
      "evidenceRefs": [
        "commercial:newMaterialCount7d",
        "commercial:ctr",
        "commercial:cpm"
      ]
    }
  },
  "highlightDashboard": {
    "groups": [
      {
        "category": "portrait",
        "frameRefs": ["highlight:素材甲:zero_second"],
        "commonConclusion": [
          {"title": "可见信号角度一", "body": "本组共同信号及信息作用，附推断边界。", "evidenceRefs": ["highlight:素材甲:zero_second"]},
          {"title": "可见信号角度二", "body": "另一项可区分的共同呈现方式及作用，不改写上一条。", "evidenceRefs": ["highlight:素材甲:zero_second"]}
        ],
        "derivativeDirections": [
          {"title": "测试问题一", "body": "指明源画面、保留项、改变变量甲、对照方式及指标。", "evidenceRefs": ["highlight:素材甲:zero_second"]},
          {"title": "测试问题二", "body": "指明源画面、保留项、改变变量乙、对照方式及指标。", "evidenceRefs": ["highlight:素材甲:zero_second"]}
        ]
      },
      {
        "category": "product",
        "frameRefs": ["highlight:素材甲:peak_top_1", "highlight:素材乙:zero_second"],
        "commonConclusion": [
          {"title": "产品呈现角度一", "body": "本组产品焦点的共同可见信号及作用，不宣称成交因果。", "evidenceRefs": ["highlight:素材甲:peak_top_1", "highlight:素材乙:zero_second"]},
          {"title": "产品呈现角度二", "body": "另一项可区分的信息组织方式及判断边界。", "evidenceRefs": ["highlight:素材甲:peak_top_1", "highlight:素材乙:zero_second"]}
        ],
        "derivativeDirections": [
          {"title": "产品测试问题一", "body": "从源画面提取可保留项，改变变量丙并明确对照及指标。", "evidenceRefs": ["highlight:素材甲:peak_top_1"]},
          {"title": "产品测试问题二", "body": "从另一源画面提取可保留项，改变变量丁并明确对照及指标。", "evidenceRefs": ["highlight:素材乙:zero_second"]}
        ]
      }
    ]
  },
  "customClassification": {
    "ruleId": "batch_visual_mechanism_v1",
    "ruleName": "本批次画面机制分类",
    "ruleDescription": "按本批次成功高光帧中可观察的主导信息任务分组",
    "ruleVersion": "v1",
    "inputFrameManifestHash": "sha256:事实包 manifest 的哈希",
    "groups": [
      {
        "groupId": "mechanism-a",
        "groupName": "机制甲的业务语义名称",
        "description": "机制甲的可见纳入条件，及不属于乙或丙的边界。",
        "frameRefs": ["highlight:素材甲:zero_second"],
        "commonConclusion": [
          {"title": "机制甲结论一", "body": "甲组第一项共同信号、信息作用和边界。", "evidenceRefs": ["highlight:素材甲:zero_second"]},
          {"title": "机制甲结论二", "body": "甲组第二项独立观察角度、信息作用和边界。", "evidenceRefs": ["highlight:素材甲:zero_second"]}
        ],
        "derivativeDirections": [
          {"title": "机制甲方向一", "body": "基于甲组源画面，保留关键元素，明确变量一、对照及指标。", "evidenceRefs": ["highlight:素材甲:zero_second"]},
          {"title": "机制甲方向二", "body": "基于甲组源画面，测试不同问题，明确变量二、保留项、对照及指标。", "evidenceRefs": ["highlight:素材甲:zero_second"]}
        ]
      },
      {
        "groupId": "mechanism-b",
        "groupName": "机制乙的业务语义名称",
        "description": "机制乙的可见纳入条件，及不属于甲或丙的边界。",
        "frameRefs": ["highlight:素材甲:peak_top_1"],
        "commonConclusion": [
          {"title": "机制乙结论一", "body": "乙组第一项共同信号、信息作用和边界。", "evidenceRefs": ["highlight:素材甲:peak_top_1"]},
          {"title": "机制乙结论二", "body": "乙组第二项独立观察角度、信息作用和边界。", "evidenceRefs": ["highlight:素材甲:peak_top_1"]}
        ],
        "derivativeDirections": [
          {"title": "机制乙方向一", "body": "基于乙组源画面，保留关键元素，明确变量三、对照及指标。", "evidenceRefs": ["highlight:素材甲:peak_top_1"]},
          {"title": "机制乙方向二", "body": "基于乙组源画面，测试不同问题，明确变量四、保留项、对照及指标。", "evidenceRefs": ["highlight:素材甲:peak_top_1"]}
        ]
      },
      {
        "groupId": "mechanism-c",
        "groupName": "机制丙的业务语义名称",
        "description": "机制丙的可见纳入条件，及不属于甲或乙的边界。",
        "frameRefs": ["highlight:素材乙:zero_second"],
        "commonConclusion": [
          {"title": "机制丙结论一", "body": "丙组第一项共同信号、信息作用和边界。", "evidenceRefs": ["highlight:素材乙:zero_second"]},
          {"title": "机制丙结论二", "body": "丙组第二项独立观察角度、信息作用和边界。", "evidenceRefs": ["highlight:素材乙:zero_second"]}
        ],
        "derivativeDirections": [
          {"title": "机制丙方向一", "body": "基于丙组源画面，保留关键元素，明确变量五、对照及指标。", "evidenceRefs": ["highlight:素材乙:zero_second"]},
          {"title": "机制丙方向二", "body": "基于丙组源画面，测试不同问题，明确变量六、保留项、对照及指标。", "evidenceRefs": ["highlight:素材乙:zero_second"]}
        ]
      }
    ],
    "unclassifiedFrameRefs": [],
    "availability": "available"
  },
  "batchInsights": [
    {
      "topic": "audience",
      "title": "洞察标题",
      "conclusion": "基于证据的谨慎结论",
      "evidenceRefs": [
        "事实包中已有的引用"
      ]
    }
  ],
  "keywordCloudInsight": {
    "analysisStatus": "supported",
    "limitation": "",
    "oneSentenceConclusion": "脚本以大促福利制造购买冲动，再用鲜炖品质与日常场景承接信任。",
    "summary": "高频表达呈现“福利促转化、鲜炖品质稳信任、早餐与三伏天强化代入”的递进结构；下一轮可对照福利强弱与品质承接比例，观察点击和成交变化。",
    "sellingPoints": [
      {
        "title": "卖点标题",
        "dimension": "实际脚本维度；此数组仅示范形状，supported 实际至少三条",
        "description": "仅基于脚本文本和关键词云提炼的卖点描述。",
        "keywordRefs": [
          "关键词云中的真实词"
        ],
        "evidenceRefs": [
          "script:materialId:text:关键词"
        ]
      }
    ]
  },
  "materialAnalyses": [
    {
      "materialId": "事实包中的素材 ID",
      "audienceConclusion": "人群分析或数据不足说明",
      "scriptConclusion": "脚本分析或数据不足说明",
      "interactionConclusion": "互动时序分析或数据不足说明",
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
      "evidenceRefs": [
        "事实包中已有的引用"
      ]
    }
  ]
}
```
