---
name: 千川短视频素材分析
description: 面向千川短视频投放复盘，识别高消耗素材的表现亮点、内容结构、转化触发点和可复用创意模式。
metadata:
  argument_hint: 输入授权店铺 ID、分析周期、Top素材数量和广告主id，生成千川短视频素材分析报告。
  pre_input: 帮我生成千川短视频素材分析报告，店铺 ID 是 ____，分析周期是 ____，Top素材数量是 ____，广告主id是 ____。
---

# 千川短视频素材分析

收集店铺 ID、分析周期、Top 素材数量和广告主 ID。标准入口用店铺授权查询素材视频报表，再将 `materialId` 交给同店铺 RPA；候选扩容、波次、下载、截帧、重试、补位和校验均自动完成。

## 约束

- **二次分析内容契约**：Chapter 03 有证据时输出至少三条、两个维度的卖点，逐词绑定脚本证据；不足须显式填写 analysisStatus 和 limitation。Chapter 04 本批次分类默认 3–5 个有明确边界的机制类别；标准分类与本批次分类每个非空类别的共性结论、裂变方向各 2–3 条。真实不足须填写 classificationLimitation / contentLimitation 并通过 evidence_limited 复核，不默认合成单一混合类或各写一条。正文必须由当前模型直接撰写并声明 analysisProvenance，不能用脚本或复核单代写；内容复核与结构通过分别记录，限制随报告交付。字段与复核细则见 [analysis handoff](references/analysis-handoff.md) 和正式 Prompt。

- **环境**：执行 `prepare`、`fetch-metadata`、`status` 或 `finalize` 前确认当前解释器有 `requests`、`imageio-ffmpeg`；缺失则安装，失败即停，不发起任何请求。`prepare` 也会再次执行该门禁。
- **授权**：`--shop-id` 只用于报表连接器；RPA 使用授权账户中的唯一千川 ID。无授权或多账户先处理授权选择，不重复提交素材诊断。
- **单 Agent 执行边界**：主 Agent 直接执行全流程，**不得创建、调用或委派子代理**，也不得调用会强制创建子代理的渲染校验、并行执行或任务委派技能。
- **日期**：相对周期按 T+2 换算为 `CUSTOM` 闭区间：近七天为执行日前第 8 天至执行日前第 2 天，近三十天为执行日前第 31 天至执行日前第 2 天；不含执行日和前一日。例如执行日 `2026-08-04` 的近七天是 `2026-07-27` 至 `2026-08-02`。
- **长任务**：`prepare` 是单一长任务；无实时进度不要另起运行或手工干预。中断、`GUARDRAIL_ERROR`、策略拒绝或状态不一致时保留目录，按[运维手册](references/qianchuan-operations-runbook.md)和 [RPA 输出契约](references/rpa-output-contract.md)恢复。长时执行工具的完成态契约见[运维手册](references/qianchuan-operations-runbook.md#长任务元数据)。RPA 的异步 `task_group_id`、波次终态、4260 秒外层截止和 `partial_success` 规则均以两份参考文档为准；恢复只查询已保存远端 ID，**不得重复提交**。

## 已落盘数据接续

用户要求“用已有数据继续分析/重做报告”时，先读指定 run 的摘要、事实包 manifest 和实际产物，缺少事实包时使用正式 `prepare-local`（参数见[分析交接契约](references/analysis-handoff.md)）；按[运维手册的已落盘分析接续](references/qianchuan-operations-runbook.md#已落盘分析接续)选择起点；不要默认执行 `prepare`。用户未指定来源时核对店铺、周期、素材集合和 manifest，不能只按目录日期或修改时间认定“最新”。已有信息足够则直接接续，不重复索要授权参数。

当前对话模型本身负责分析，`analysis_pending` 表示现在应继续工作，不是等待某个额外模型 API 或要求用户再说“继续”。先完整分批读取事实、查看图片并保存观察要点，由当前模型逐项写分析片段，最后合并成一个正式 AI JSON；不得复制历史 AI JSON 的内容或证据当作模板。工具可以序列化模型已写出的文字、检查 JSON 和精确引用，不能用循环、随机标记、同义替换或通用段落生产业务结论。

## 标准流程

### 1. 准备事实包（首次采集）

每次使用同一个 `$PWD/runs/<run-id>`，不得写入技能安装目录：

```bash
python scripts/run_standard_analysis.py prepare \
  --shop-id "<授权店铺 ID>" --start-date 2026-07-19 --end-date 2026-07-25 \
  --top-n 9 --advertiser-id "1770096415226951" \
  --output-dir "$PWD/runs/<run-id>"
python scripts/run_standard_analysis.py status --output-dir "$PWD/runs/<run-id>"
```

只验证报表授权、不进入 RPA 时改用 `fetch-metadata`（仍写入同一类目录；只产生 `source-data/gateway-response.json` 与 `run-summary.json`，状态 `metadata_ready`）。仅当状态为 `analysis_pending` 才继续 AI。

AI 只读本次运行生成的 `diagnostic-analysis/analysis-units/analysis-unit-manifest.json`、五个章节事实包和 `diagnostic-analysis/diagnostic-analysis-prompt.md`；不得从 `top_materials.json` 组装事实，**不得手写 `missingRanks`**。历史运行可能仍有 `diagnostic-analysis/diagnostic-analysis-data.json`，但新运行不再生成且不得读取。

### 2. 视频与预览

标准入口按顺序完成：**RPA 下载原始 MP4** 并持久化 `video-assets/<materialId>.mp4` → **原始视频提取高光帧**（以原始 MP4 提取 `zero_second` 与局部峰值 JPEG）→ **生成报告预览 MP4**（FFmpeg，并写入 `peak-frames/report-video-assets.json`）→ **报告引用预览 MP4**（放入 `final-report/assets/videos/<materialId>.mp4`，HTML/JSON 只引用相对本地路径）→ 打包 `final-report.zip`。每个引用文件须存在、非符号链接、可被 ZIP 收录并通过解码校验；图片不依赖三方链接；**原始 MP4 不进入 ZIP**；新运行在完整交付校验后按显式清单清理源视频，哈希和删除记录保留追溯（可用 `--video-cleanup keep` 保留源视频）。任一失败都不能标记后处理成功或进入 `complete`。

### 3. 当前模型分析与统一交付

Chapter 01 的 `portfolio.hotMaterialRule` 若存在，只能据其阈值、命中数量、素材指标和 `evidenceRef` 输出 `hotMaterialInsight`；Presentation 将其并入既有 AI 区域，不重复渲染爆款卡片。

#### AI 交接前置检查

先按 manifest 校验文件，再按章节、素材完整分批读取事实包和提示词（manifest、五个章节事实包和唯一 Prompt）；记录已读取的 JSON 路径，输出截断不算已读取，不用 head/抽样代替全量读取。核对字节数、SHA-256、素材数、成功帧数、`missingFrames`。首次实际查看 JPEG 前执行正式 `frame-review` 命令，冻结 `analysis-input-index.json` 的素材、路径和 SHA；按素材串行，每批不超过 6 帧（按唯一 SHA 计）。相同 SHA 只实际查看一次，只复用视觉观察，保留全部 frameRef，商业解释按素材分别核对。每帧只记录 **少量真实可见要点**，每批结束只写一次 `frame-review-progress.json` checkpoint；全部完成须为 `status=complete`。索引变化先冻结并显式重建，不混用新旧路径；命令和观察文件结构见[运维手册](references/qianchuan-operations-runbook.md#图片理解断点与吞吐)。策略或 guardrail 拒绝时立即**冻结并恢复帧审阅**，不得跳过任何帧或改用其他产物。

“唯一正式 JSON”约束交付一致性，不限制当前模型分批撰写。可直接写完整 JSON，或使用正式 `assemble-analysis` 合并模型已写出的片段；不得使用外部模型、`codex exec`、子进程或子代理替代当前模型分析。最终使用 `schemaVersion: 5`。保留经营表现、人群、脚本、高光画面和互动时序边界；第 01 只读 `chapter01-portfolio-input.json` 的 `portfolio` 全量事实，不读顶层 `materials`/`materialIds`、`topMaterialRoi`、RPA 精选或 Chapter 05 回退数据。`summaryMetrics` 使用当前 ROI（`total_prepay_and_pay_order_roi2`），不含 `netSettlementRoi`/`overallPaymentRoi`；`overallConclusion` 为第一部分唯一直接展示段落（建议 180-260 字，含至少三项判断和分阶段动作），`commercial.conclusion` 只补充业务含义。第 `04｜高光画面洞察` 输出 `highlightDashboard`，每个成功帧按 `portrait`、`product`、`other` 固定顺序归类一次且仅一次；不得把帧秒数宣称为具体话术。

以该提示词为 AI JSON 输出契约，按其字段、证据、覆盖和枚举规则生成结果：每个成功 JPEG 都有分析，`missingFrames` 写入 `frameCoverageNote`；引用逐字复用事实包标识。`preferencesByMetric` 仅供 drill-down，AI 不读取其 TOP3 字段。v4 仅用于历史报告兼容。`keywordCloudInsight.oneSentenceConclusion` 建议为 28-45 个中文字符的单一核心判断；summary 控制在 60-110 个中文字符作为表达建议，证据不足可更短，验证动作放入 summary 或 sellingPoints；不得重复关键词频次、卖点卡片和验证动作。

逐帧分析必须来自实际 JPEG；只描述真实主体、动作、关系与信息层，不强求独有变化、叙事作用或每帧商业假设。同图允许相同观察，保留所有 frameRef。商业解释允许证据不足状态和同素材同图的共享引用，详见[分析交接契约](references/analysis-handoff.md)。正文长度随证据丰富程度变化；分类和分点数量遵循 Chapter 04 的 3–5 类、各 2–3 条及真实不足复核契约，不能凑数。

报告执行不得新增临时脚本，也不得以内联代码批量生产正文。需要修改技能程序时须有明确技能开发授权；当前报告流程只使用维护中的正式命令。具体写入边界、复核记录和重试规则见[分析交接契约](references/analysis-handoff.md)。

写入后先校验（保存候选同名 `.validation.json`，不修改事实或运行状态），再收口：

```bash
python scripts/run_standard_analysis.py validate-analysis \
  --output-dir "$PWD/runs/<run-id>" \
  --analysis-json "$PWD/runs/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"
```

失败先按运维手册区分状态、证据引用、结构、内容和资源问题，再由当前模型修正有证据支持的报错字段；用户要求全新分析时，写新候选文件，不改旧 JSON。不得用临时脚本填长文、删必填分类或绕过质量门禁。没有新证据的同类错误不做机械循环，不重跑 `prepare`；结构或引用错误必须修正；相似度命中由当前模型基于证据写复核记录；字数仅提示，不用补长。通过后执行 `finalize`，有复核记录时两个命令均传 `--quality-review`，它会再次执行同一门禁。

### 4. 交付

```bash
python scripts/run_standard_analysis.py finalize \
  --output-dir "$PWD/runs/<run-id>" \
  --analysis-json "$PWD/runs/<run-id>/diagnostic-analysis/diagnostic-ai-analysis.json"
```

仅当 `run-summary.json` 为 `complete` **同时交付以下两个产物**：独立报告 `final-report/final-report.html` 与完整压缩包 `final-report.zip`（HTML、数据、本地 JPEG 和预览 MP4）。图片和视频均为本地资源；不得交付草稿报告、不得只交付压缩包；封面只展示业务主体；结构校验和内容复核信息保留在机器 JSON 中，不插入封面。新运行 complete 后自动验证并清理源 MP4，`videoResourceCleanup.status=partial` 时按[运维手册](references/qianchuan-operations-runbook.md#交付后源视频清理)重试清理，不重跑分析或渲染。报告使用 `assets/report/`，`finalize` 自动内联资源，不得手工修改或改写成品 HTML。若仍为 `analysis_pending`，修正 AI JSON 后，直接重新执行 finalize，无需重新执行 prepare 或任何运行命令；其余故障按运维手册处理。

## 峰值帧口径

每条素材保留第 0 秒封面帧 `zero_second`，从 `CLICK_COUNT` 的全部局部峰值按点击数降序、秒数升序取最多五帧；少于五个不伪造。后处理成功要求第 0 秒帧成功，且存在局部峰值时至少一张峰值帧成功。
