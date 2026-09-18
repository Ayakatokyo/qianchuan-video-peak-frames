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

当前唯一默认流程使用精简分析任务，五章原始事实和全部成功 JPEG 继续完整保留。原有 analysis-plan/read-analysis 提供无损全文溯源，但不是默认模型阅读路径。

```bash
python scripts/run_standard_analysis.py analysis-task --output-dir "$PWD/千川数据资源/<run-id>"
python scripts/run_standard_analysis.py analysis-task --output-dir "$PWD/千川数据资源/<run-id>" --task chapter01 --page 0 --input-sha256 "<计划哈希>"
```

按 nextPage 读到 null。每页 records 序列化后不超过10000字节、普通内容行不超过1200字节，外层元数据另计。records 用 path/value 表示值，container 初始化大对象、数组或字符串，text 按 start 拼接。截断页不得算已读。inputSha256 绑定事实、图片、Prompt和任务实现；自定义分类仍使用 sha256:<manifestSha256>。

每个任务给出 scopedFacts、outputSkeleton、allowedEvidenceRefs 和写作预算。骨架中的 null 必须由模型写入，空引用必须从当前合法引用选择，不能把骨架作为成品提交。顺序为 chapter01、chapter02、chapter03、material-001…、chapter04。每张唯一图片实际观察后，同次完成该素材及第四章所需观察，不写中间长篇视觉报告。

- chapter01：完整聚合、爆款和全量成本分布，另给消耗前10项；sourceCount/missingCostCount与读取边界明确；缺失成本不当零，ROI不由局部列表重新计算。
- chapter02：三个人群口径与全部维度及原分母保留；超过8个桶的维度只读原顺序前5桶，bucketReadingCoverage标明省略量，不能重算总体比例；三条洞察各自绑定 SHOW_COUNT、CLICK_COUNT、TRANSACTION_AMOUNT。
- chapter03：完整脚本、标签、关键词与证据；保留一二级内容公式和脚本文案两条洞察。
- material：全部脚本、帧、缺失和证据，时序保留峰值与点数；原points在事实包供报告曲线。不得据精简视图推断完整走势，必要时按sourceFile/sourcePath定向读取。
- chapter04：全部成功帧的两套分类，每组默认一条共性判断和一个方向，不能遗漏frameRef。

```bash
python scripts/run_standard_analysis.py check-analysis-part --output-dir "$PWD/千川数据资源/<run-id>" --part "<本次片段.json>"
```

命令前置章节洞察数量、引用、人群口径及单素材结构／帧覆盖检查。contentReviewed=false和finalValidationRequired=true说明仍须最终assemble-analysis、validate-analysis、内容复核和finalize，不证明实际看图。失败只修受影响片段。

任务计划记录sourceBytes、taskBytes、pageCount及prepareSeconds；字节与本地准备时长不能直接换算AI提速。旧批次只读接续；新Prompt对照需在独立副本中重新冻结，不覆盖原事实、候选或报告。

## 写入边界与分片

报告执行只写本次 run 的约定数据产物：图片观察账本、模型分析片段、候选 JSON、合并来源记录、质量校验与复核记录、正式报告及资源。不得新增或执行临时 `.py/.sh/.js`，也不得用内联 Python、shell、JavaScript 的循环或字符串模板生成业务正文。报告请求不等于技能开发授权；缺少程序能力时记录缺口，只有用户明确授权修改技能才编辑维护中的程序。

当前模型按章节、素材阅读正式任务事实视图并查看全部成功 JPEG，直接用文件编辑工具写 JSON。使用正式 `frame-review` 命令冻结索引并恢复下一批，每批最多 6 个唯一 SHA。当前模型将该批实际观察写成 JSON 后提交一次 checkpoint，程序原子维护 `visualObservationCache`、`frameRefsBySha256`、逐引用覆盖和 complete 状态，不手工 patch 账本。只复用视觉观察，不跨素材复用商业解释；完整命令见[运维手册](qianchuan-operations-runbook.md#图片理解断点与吞吐)。业务解释可以随素材审阅逐项撰写，不要求等到单次巨型输出。

片段置于 `diagnostic-analysis/parts/`。章节片段包含自己负责的顶层字段；素材片段包含 `materialAnalyses` 数组。数组由正式程序连接；标量/对象顶层字段只允许一个来源，冲突报错，绝不自动补写缺失内容。Chapter 04 的逐帧分析统一放在相应的 Chapter 05 素材片段中，避免同一素材重复合并。

```bash
python scripts/run_standard_analysis.py assemble-analysis \
  --output-dir "$PWD/千川数据资源/<run-id>" \
  --part "$PWD/千川数据资源/<run-id>/diagnostic-analysis/parts/chapters.json" \
  --part "$PWD/千川数据资源/<run-id>/diagnostic-analysis/parts/materials.json" \
  --analysis-json "$PWD/千川数据资源/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"
```

必须显式列出片段，不使用 glob/latest。程序核验事实 manifest、完整素材集合和重复字段，保持模型原文，拒绝覆盖已有候选，保存 `.assembly.json` 来源哈希。允许直接写完整 JSON；两种方式都必须执行标准校验。

校验和 finalize 会核对已有 assembly 记录中的候选、输入、Prompt 和全部片段哈希；组装后改写候选或片段会失败。不要删除来源记录求通过，应保留原文件并生成新候选。这仍不能证明没有人伪造来源声明，需结合真实工具日志审阅。

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

对确有证据支持或存在共同证据限制的相似内容，当前模型直接写 `quality-review.json`，无需用户重复批准。复制校验结果中的 `policyVersion`、`analysisSha256`、`inputSha256`，并逐项写：

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

校验和收口都传入同一个 `--quality-review <文件>`。候选、事实或 JPEG 变化后旧复核失效，重新审阅。没有 review 项则不需要此文件。`finalize` 再执行同一门禁，并核对候选哈希；绝不使用 `require_ai_quality=false`。

同类失败只有新增证据或实质修正后才重试；无新信息时保留候选和具体冲突，不做机械循环。历史事实、提示词快照和失败草稿保留追溯，不在报告任务中偷偷更新旧快照。

## Chapter 04 数量与分析深度门槛

新 Prompt 包含 `highlight-depth-policy: mechanism-depth-v1` 标记，validate-analysis / finalize 据本轮冻结的 Prompt 启用 3–5 类、每组两部分默认各一条（结构兼容 1–3 条）的校验；旧 Prompt 保留历史兼容。数量满足后仍执行机制边界、证据充分性、方向可测试性的内容复核，程序不自动拆组或生成正文。共性结论仍至少保留一条有依据的观察或具体证据限制；裂变方向确无依据时可为空数组，须填写 contentLimitation 并复核。两种分类均完整投影全部条目，不在前端补写。

如果要用新规则重新分析旧输入，应在新 run/隔离副本生成新 Prompt、重建帧审阅索引、写新候选和新复核记录。不能只替换旧 Prompt 后沿用旧哈希复核单，也不能手工修改最终 HTML 来扩充条数。
