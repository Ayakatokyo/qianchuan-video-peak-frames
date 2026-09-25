# 本地接续、分片分析与质量复核

## 正式本地入口

已有 RPA records、视频、JPEG 和批次核验收据，缺少五章事实包时使用：

```bash
python scripts/run_standard_analysis.py prepare-local \
  --shop-id "<已确认店铺>" --advertiser-id "<广告主>" \
  --start-date 2026-08-30 --end-date 2026-09-06 --top-n 10 \
  --output-dir "$PWD/千川数据资源/<run-id>"
```

入口只使用已安装的依赖，不自动联网安装。入口校验原始榜单与精选名单的完整 portfolioFacts、周期、广告主、TopN、records、summary 和实际 JPEG；请求与 `run-summary.request` 不一致时，错误会列出每个差异字段的 saved/current 值，按差异补齐参数后再执行。新事实包先完整构建再发布；现有事实包只核验，不覆盖。首次 prepare 和 prepare-local 生成 schemaVersion=2 的 analysis-input-index.json 及 frame-review-progress.json，冻结实际 JPEG 路径、materialId、哈希、Prompt 和事实 manifest；不含业务结论。已有 `complete`、正在运行或部分损坏的事实包保留原状，在明确指定的隔离副本中恢复，禁止临时调用私有函数改状态。无核验收据时先完成原有批次核验，不重复提交 RPA。

## 无损读取与执行顺序

默认使用 `analysis-session`，由当前模型完成 Chapter 01 → Chapter 02 → Chapter 03 → 各 material → Chapter 04；入口不调用外部模型，不创建子代理。它把每次工作限制为当前任务，并复用原事实、校验器和图片观察账本。默认会话目录为 `diagnostic-analysis/analysis-session/`。

以下三种调用分别领取/恢复当前任务、保存一小组观察、提交模型写好的片段；JSON 均由当前模型通过文件工具直接写在本次 `diagnostic-analysis/` 内，不能用临时脚本生成正文：

```bash
python scripts/run_standard_analysis.py analysis-session --output-dir "$PWD/千川数据资源/<run-id>"
python scripts/run_standard_analysis.py analysis-session --output-dir "$PWD/千川数据资源/<run-id>" --observations-json "$PWD/千川数据资源/<run-id>/diagnostic-analysis/observations.json"
python scripts/run_standard_analysis.py analysis-session --output-dir "$PWD/千川数据资源/<run-id>" --submission-json "$PWD/千川数据资源/<run-id>/diagnostic-analysis/submission.json"
```

返回 `taskId`、`inputSha256`、`factsPages`、`contractPages`、`submissionTemplatePath` 和 `contextHandoff.resumePath`。完整读取当前任务所有 factsPages；每种输出结构首次读 contractPages，不能把骨架中的 null 当成成品。Chapter 01 的聚合引用读返回的 `referencePages`；其他引用按事实中的 evidenceIndex/evidenceRef 和 contract 使用，必要时通过兼容 `analysis-task --section references` 定向导出。引用并集不意味着所有字段都接受所有引用。素材 decision 只能取 `scale/remix/hold/stop/unknown`；标准分类 category 只能取 `portrait/product/other`。customClassification 与 highlightDashboard 顶层同级；keywordRefs 逐字取原词并绑定该词自身的脚本证据。

若工作项过长，响应返回 `workItemPages`：先完整读这些页恢复工作项，再读取其 factsPages 和当前图片组，不能只读短索引。facts/contract/references/work-item 的每个页文件，包括封套、缩进及换行，都不超过 **8000 UTF-8 字节**，页索引含 byteSize/SHA-256。按 nextPage 读到 null；截断不算已读，不用 head/tail 或临时 jq 抽样代替当前任务的完整读取。

新页首条 record 标注 `encoding: lossless-tables-v1` 与 readingGuide。先按 path/value、container/start/items/text 还原记录树；`$table.columns` 给出列顺序和嵌套字段路径，common 用从零起的列序号存共同值，每行只提供其余列；kind=array 保留行顺序，kind=object 将 keys 与 rows 对齐；`$literal` 保留原始保留键对象，不再解码。此表示无损保留原 `model_view` 的全部事实、类型、顺序和证据引用，不增加采样或删减。原五包继续作为完整来源；不要假设值总在 `.records[0].value`。旧无编码页、`analysis-task --section facts --export-dir <分析目录内新目录>` 及 `analysis-plan/read-analysis` 仍兼容，分别用于旧流程接续和定向全文溯源，不与新会话反复重复导出。inputSha256 绑定事实、图片、Prompt 和读取/会话实现；自定义分类仍使用 `sha256:<manifestSha256>`。

- chapter01：完整聚合、爆款和全量成本分布，另给消耗前10项；sourceCount/missingCostCount与读取边界明确；缺失成本不当零，ROI不由局部列表重新计算。
- chapter02：三个人群口径与全部维度及原分母保留；超过8个桶的维度只读原顺序前5桶，bucketReadingCoverage标明省略量，不能重算总体比例；三条洞察各自绑定 SHOW_COUNT、CLICK_COUNT、TRANSACTION_AMOUNT。
- chapter03：完整脚本、标签、关键词与证据；保留一二级内容公式和脚本文案两条洞察。`batchInsights` 仅用 `evidenceRules.batchInsights.allowedEvidenceRefs` 中的标签型引用；关键词文本引用只用于绑定自身关键词的卖点，来源为 `keywordCloud.keywords[*].evidenceRefs`。总 `allowedEvidenceRefs` 表示本任务引用并集，不代表每个输出字段都接受所有引用。
- material：全部脚本、帧、缺失和证据，时序保留峰值与点数；原points在事实包供报告曲线。不得据精简视图推断完整走势，必要时按sourceFile/sourcePath定向读取。
- chapter04：全部成功帧的两套分类，每组默认一条共性判断和一个方向，不能遗漏frameRef。

会话当前任务的 `submissionTemplatePath` 给出提交外壳：`{"inputSha256":"返回的绑定","taskId":"当前任务","part":{模型直接撰写的字段}}`。章节 part 仅包含该章字段；素材 part 包含仅当前素材的 `materialAnalyses` 数组。每个片段仍须满足下方完整内容契约。提交一次完成片段预检、必要的帧 checkpoint、原文保存和下一任务导航；缺图、错误归属、引用/结构错误不会推进任务。不能隐藏 stderr 或只看管道末尾的退出码。

素材的 `frameReview.visionBatches` 将当前待看图片分为每组最多 **3 张**；账本新批次最多 **6 个唯一 SHA**，旧 **4 帧**账本按原冻结批次续接。每看完一组就用 `--observations-json` 保存，不等待整素材，更不等上下文压缩后再回读工具日志。观察 JSON 与 checkpoint 形状相同：`inputIndexSha256`、当前批次全部 `frameRefs`、`observations`；observations 只写本组实际看的唯一 SHA，每条为 `sourceFrameRef/visualObservation`，用少量真实主体、动作/状态和信息层要点，不能提交 null。暂存不会把尚未看的帧标为完成，返回状态只列剩余 SHA。最后一组可作为提交外壳的可选 `frameObservations`，与 part 一次提交；它和 `--observations-json` 不能同时使用。

已全缓存无需再看，程序在素材提交时推进缓存覆盖；不能跨素材照抄商业解释。Chapter 04 的 factsPages 增加 `reviewedVisuals`，包含完整 SHA 观察及全部对应 frameRefs；据此核对两套分类，疑义或复核命中时定向看对应 JPEG，不重新打开整批图片。观察来自真实工具，SHA 和笔记本身不能证明已经看图。

普通接续只重跑无提交参数的 analysis-session，读取当前工作项和 `contextHandoff.resumePath` 对应的 `resume.json`。完成的事实、图片观察及片段留磁盘，不再塞回主上下文。相同 submission 和已保存 stash 可幂等重试；中断后不用重新撰写已接受片段。`elapsedSeconds` 是任务激活到接受提交的墙钟，包含 idle，不是模型推理时间；字节数和本地准备耗时也不等于 token 或 AI 提速。

已接受片段需要实质修改，或输入/实现 hash 变化时，保留旧会话，并用 `--session-dir <diagnostic-analysis 内的新目录绝对路径>` 新建会话。JPEG/事实/Prompt 变化还须先按冻结规则在隔离副本中核验和重建索引；不能改写旧 Prompt 快照或清空账本混用新旧证据。宿主提供正式上下文压缩能力时，可在素材成功提交后仅保留恢复指针、当前任务与未解决问题再继续；本技能不能强制清空宿主历史，不提供伪造的释放命令，也不新建子代理。

所有任务接受后，入口正式组装候选，返回 `status=ready_for_validation` 与 `analysisJson`。这只表示候选已生成；继续使用该路径执行原 validate-analysis、内容复核和 finalize，不能直接交付。结构预检的 `contentReviewed=false`、`finalValidationRequired=true` 也不证明内容正确或实际看过图。

## 写入边界与分片

报告执行只写本次 run 的正式数据产物：图片观察账本、会话工作页/恢复收据、模型片段、候选 JSON、来源记录、质量校验与复核记录、正式报告及资源。当前模型使用文件工具直接写 JSON；不得新增或执行临时 `.py/.sh/.js`，也不得用内联 Python、shell、JavaScript 的循环或字符串模板生成业务正文。报告请求不等于技能开发授权。

新会话片段保存在会话目录的 `parts/`。不手工修改已接受文件或账本，程序连接数组并检查顶层冲突，不自动补业务正文。旧流程仍可将模型片段写入 `diagnostic-analysis/parts/`，以 `check-analysis-part --part <文件>` 预检，多个片段重复传 --part；再用正式 `assemble-analysis` 显式列出片段并生成新候选，不能用 glob/latest。Chapter 04 逐帧分析只放对应的素材片段，避免重复合并。

组装来源记录绑定候选、输入、Prompt 和全部片段哈希，后续改写会被拒绝；保留原文件并在新会话生成修正候选，不能删除来源记录求通过。来源声明和哈希不能单独证明模型真实分析，须结合实际工具日志。

## 输出按证据丰富程度变化

长度是表达建议。Chapter 03 使用 analysisStatus、limitation 和含 dimension 的卖点卡片：supported 至少三条、两个不同维度，逐词绑定自身 script 引用；不足则明确 insufficient_evidence 和缺口。Chapter 04 本批次分类默认按主导画面机制拆成 3–5 类，description 说明纳入条件及类别边界，不能把不同信息任务收进“人物/产品/操作混合”的总类。标准分类和本批次分类每个非空组的 commonConclusion、derivativeDirections 默认各一条（结构兼容 1–3 条），逐条绑定组内真实帧，检验不同的信息作用和具体可测试变量；同义改写或重复正文不算新增条目。真实不足时，类别少于 3 个用 customClassification.classificationLimitation、某组缺少有依据的条目用该组 contentLimitation 说明具体证据缺口，必须以 evidence_limited 完成独立复核，不能用 supported 普通放行。卖点语义与各机制组始终进入内容复核，不依赖相似度命中，不能靠同义改写消除 review。

新候选必须声明 `analysisProvenance: {"author":"current_model","businessTextGeneration":"direct"}`，正式组装保留片段及候选哈希。声明不证明来源真实；脚本改写过的候选必须留档并由当前模型重新分析，不能补声明放行。不得用质量复核单代写业务正文。

逐帧观察仍必须覆盖每张成功 JPEG。商业解释支持三种形式：

- 默认 `interpretationStatus: "supported"`（可省略）：填写 `commercialInterpretation`。
- `interpretationStatus: "insufficient_evidence"`：`commercialInterpretation` 为空，填写具体 `limitation`。前端显示“证据不足”及原因。
- `interpretationStatus: "shared"`：`commercialInterpretation` 为空，填写 `sharedInterpretationRef`。只允许同一素材、SHA-256 相同的 JPEG 引用一条直接的 supported 解释，禁止链式、循环和跨素材共享。前端展示被引用解释并注明共用。跨素材相同图片只能共享观察，商业解释仍核对各自事实。

## 校验和当前模型复核

`validate-analysis` 保存候选同名 `.validation.json`；不改变运行状态。退出码 0 表示通过，2 表示结构/引用/资源等错误，3 表示需要内容复核。不要通过 `tail` 管道丢失原始退出码；直接读取结构化文件。

- `error`：引用、素材归属、口径、覆盖、资源等硬错误。修正对应字段或输入，不能用复核记录豁免。
- `review`：文本相似等启发式信号。读取 `field`、`evidenceRefs`、`examples` 和 `count`，回到 JPEG/事实复核该类全部命中，代表样例不替代全范围判断。无依据套话必须重写，禁止补字和改词求通过。
- `advice`：字数、关键词一句话写法等表达建议。短而充分的结论可直接交付，不要求逐条消除建议。

首次 `validate-analysis` 加 `--review-template <新文件>`：仅当结构通过且需要复核时，自动复制绑定哈希、所有 review 项的完整 evidenceRefs、field/message/count/examples 和 requiredDecision；decision 留 null，reason 留空。程序不生成结论、不替模型放行，拒绝覆盖已有文件。模型读取各项说明、回查整个证据集合后，仅填写 decision/reason；引用已完整保存在文件，无需打印和手抄上千条引用。需要再次查看结果时从 `.validation.json` 的 `findings` 按 level 筛选，文件没有顶层 review 数组。无复核项时不产生模板。

对确有证据支持或存在共同证据限制的相似内容，无需用户重复批准。手动写复核记录时，其最小结构如下，evidenceRefs 必须与该项集合完全一致（不能只传子集）：

```json
{
  "policyVersion": "evidence-first-v2",
  "analysisSha256": "校验返回值",
  "inputSha256": "校验返回值",
  "findings": [
    {
      "id": "校验返回的 review id",
      "decision": "supported",
      "reason": "基于哪些实际画面和本素材事实，为什么相似表述在此成立；不以字数或通过门禁为理由",
      "evidenceRefs": ["此复核项的全部实际引用"]
    }
  ]
}
```

`decision` 仅允许 `supported` 或 `evidence_limited`。必须核对整个复核项的证据集合；不能只看一个样例就整批放行。复核文字是真实审阅记录，哈希和字段校验本身不能证明分析真实。

structurally_valid 与 content_reviewed 分别表示结构通过和复核完成；contentReviewStatus=reviewed_with_limits 保留 evidence_limited 的理由及引用到运行摘要、Presentation 和报告 JSON（HTML 中的机器数据可保留，但封面不渲染这些质量门禁文字）。complete 只表示流程完成，不代表无内容限制。表达 advice 无需清零。硬错误即使有复核记录也不能豁免。

模板填完直接 `finalize --quality-review <文件>`；finalize 内部重新完整校验，未通过时不会生成报告，无需在它之前再做一次同输入的 validate-analysis。若单独排查仍可执行 `validate-analysis --quality-review <文件>`。候选、事实或 JPEG 变化后旧复核失效，重新审阅。没有 review 项则不需要此文件。`finalize` 再执行同一门禁，并核对候选哈希；绝不使用 `require_ai_quality=false`。

同类失败只有新增证据或实质修正后才重试；无新信息时保留候选和具体冲突，不做机械循环。历史事实、提示词快照和失败草稿保留追溯，不在报告任务中偷偷更新旧快照。

## Chapter 04 数量与分析深度门槛

新 Prompt 包含 `highlight-depth-policy: mechanism-depth-v1` 标记，validate-analysis / finalize 据本轮冻结的 Prompt 启用 3–5 类、每组两部分默认各一条（结构兼容 1–3 条）的校验；旧 Prompt 保留历史兼容。数量满足后仍执行机制边界、证据充分性、方向可测试性的内容复核，程序不自动拆组或生成正文。共性结论仍至少保留一条有依据的观察或具体证据限制；裂变方向确无依据时可为空数组，须填写 contentLimitation 并复核。两种分类均完整投影全部条目，不在前端补写。

如果要用新规则重新分析旧输入，应在新 run/隔离副本生成新 Prompt、重建帧审阅索引、写新候选和新复核记录。不能只替换旧 Prompt 后沿用旧哈希复核单，也不能手工修改最终 HTML 来扩充条数。
