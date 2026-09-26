---
name: 千川投放素材全景分析
description: 面向千川短视频投放复盘，识别高消耗素材的表现亮点、内容结构、转化触发点和可复用创意模式。
metadata:
  argument_hint: 依次选择报表授权店铺和千川广告主 ID，输入时间范围和详情素材数，生成千川投放素材全景分析报告。
  pre_input: |
    请采集千川素材及相关诊断数据
    千川授权店铺：__field:api_shop__（必填）
    千川广告主ID：__field:rpa_shop__（必填）
    时间范围：__field:date_range__
    详情素材数：__field:top_n__（包含互动时序、人群、脚本和高光帧数据，默认10）
---

# 千川投放素材全景分析

收集报表授权店铺、千川广告主 ID 对应的 RPA 授权账号、分析周期和 Top 素材数量。标准入口先用 API 授权店铺及解析出的广告主 ID 查询素材视频报表，再将 `materialId` 交给用户选定的 RPA 授权账号；单素材有效 CSV 和可用 records 落盘、满足原入选条件后，立即进入有界本地后处理队列，不等待其他素材的 RPA。RPA 仍默认并发 3、按波次屏障推进下一批；视频下载并发 2、FFmpeg 并发 2，与后续 RPA 重叠执行。首次 `prepare` 和标准 `resume` 共用该调度；独立本地修复的适用边界见运维手册。

候选扩容、波次、下载、截帧、本地后处理重试、补位和校验均自动完成，每个素材的 RPA 只提交一次。候选耗尽但最终成功数量不足时保留已完成的本地成果并停止，不能标记 ready；只有目标素材的后处理全部成功并通过核验，才进入事实包与分析阶段。

表单不再单独收集 `advertiser_id`。首次 `prepare` 或 `fetch-metadata` 在报表取数前按所选 `rpa_shop.extra.shop_id` 查询当前 RPA 授权列表，要求唯一记录的 `account` 是数字广告主 ID，再确认它唯一出现在所选 `api_shop.extra.shop_id` 的 API 广告主授权关系中；无法确认则停止，不提交报表或 RPA 任务。`resume` 可从匹配的运行摘要读取已确认的广告主 ID。`api_shop.extra.shop_id` 与 `rpa_shop.extra.shop_id` 属于不同 ID 域，**不得比较是否相等**。

## 约束

- **二次分析内容契约**：Chapter 03 有证据时输出至少三条、两个维度的卖点，逐词绑定脚本证据；不足须显式填写 analysisStatus 和 limitation。Chapter 04 本批次分类默认 3–5 个有明确边界的机制类别；标准分类与本批次分类每个非空类别的共性结论、裂变方向默认各一条（结构兼容 1–3 条）。真实不足须填写 classificationLimitation / contentLimitation 并通过 evidence_limited 复核，不把不同机制合成单一混合类。正文必须由当前模型直接撰写并声明 analysisProvenance，不能用脚本或复核单代写；内容复核与结构通过分别记录，限制随报告交付。字段与复核细则见 [analysis handoff](references/analysis-handoff.md) 和正式 Prompt。

- **环境**：运行于支持 POSIX flock 的 Linux/macOS 文件系统。`prepare`、`fetch-metadata`、`finalize` 入口自动检查核心依赖；`finalize` 默认额外检查 `openpyxl` 和 `Pillow`；显式 `--skip-workbook` 时省略 Excel 依赖。缺失则安装，失败即停。标准流程无需额外执行一轮 import 检查。`status` 是轻量只读入口，不加载报告依赖。
- **授权**：`--shop-id` 只用于报表连接器；`--rpa-shop-id` 只使用表单 `rpa_shop` 所选的千川 RPA ID。省略 `--advertiser-id` 时由正式入口按授权关系解析；无授权、账号不是数字 ID 或所选 ID 不在列表时停止，不重复提交素材诊断。
- **单 Agent 执行边界**：主 Agent 直接执行全流程，**不得创建、调用或委派子代理**，也不得调用会强制创建子代理的渲染校验、并行执行或任务委派技能。
- **日期**：相对周期按 T+2 换算为 `CUSTOM` 闭区间：近七天为执行日前第 8 天至执行日前第 2 天，近三十天为执行日前第 31 天至执行日前第 2 天；不含执行日和前一日。例如执行日 `2026-08-04` 的近七天是 `2026-07-27` 至 `2026-08-02`。
- **长任务**：`prepare` / `resume` 优先交给运行面已支持的持久执行会话，将日志写入运行目录。只有确认后台进程能跨工具调用存活时才用 `setsid` 脱离 Bash 会话；它不能保证避开容器回收或资源限制。不因工具会话结束而重提任务。进度同时写入 `peak-frames/batch-manifest.json` 与紧凑的 `peak-frames/batch-progress.jsonl`，后续仅用 `status.nextAction` 决定等待、恢复、分析或交付，不组合多轮 `sleep` / `jq` / 进程扫描。中断、`GUARDRAIL_ERROR`、策略拒绝或状态不一致时保留目录，按[运维手册](references/qianchuan-operations-runbook.md)和 [RPA 输出契约](references/rpa-output-contract.md)恢复。长时执行工具的完成态契约见[运维手册](references/qianchuan-operations-runbook.md#长任务元数据)。RPA 的异步 `task_group_id`、波次终态、4260 秒外层截止和 `partial_success` 规则均以两份参考文档为准；恢复先查询已保存远端 ID，**不得重复提交**。

## RPA 动态参数契约

素材详情连接器 `rpa_rpa.conn.juliang.qc.material.analysis.video.live.diagnosis` 必须先完成首批 CSV 门禁：首批为本运行前 `min(RPA并发数, 目标素材数, 候选数)` 条，默认并发 3；即使非波次调度也不得在首批判定前补位。首批全部已终态且没有任何正确 CSV 时，持久化 `firstBatchCsvGate.status=blocked` / `errorCode=first_batch_no_valid_csv`，暂停本次运行后续所有 RPA 提交、重试、补位与自动接续，并提示“当前入参可能有问题”，核对授权账号、日期和业务参数。有效 CSV 须为本次请求落盘、非空可解析、含身份匹配的 `material_info`；只有下载链接、空表、解析失败或其他素材的数据均不算。缺视频 URL、视频下载或抽帧失败单独处理，不等于没有 CSV。

首批任一 CSV 有效且全员已终态时放行；异步 pending/running、轮询失败、超时或中断不能认定远端失败，首批结果未确认时持久化 `unconfirmed`，暂停后续提交并保留远端任务号。`status` 对暂停返回 `workflowStatus=paused`；`nextAction=check_parameters` 要求核对修正入参并新建运行目录重新执行首批，普通 `resume` 或同目录 `prepare` 均不能解除 blocked。`nextAction=check_remote_status` 时只允许 `resume` 续查已有任务号；没有可确认任务号则保持暂停，不重提。不得删改门禁或通过新循环、补位入口绕过。

首批 CSV 门禁和 RPA 波次屏障限制后续 RPA 提交，不阻止已核验单素材的本地处理；该素材须已有有效 CSV、落盘的可用 records 和视频 URL。首批其他任务尚未终态时可先下载、截帧，但不能因此提前放行下一批 RPA 或把整批标记 ready。

标准 `prepare` 已自动完成授权列表检查、实时 connector 详情反查和业务参数校验，再走异步 fetch、轮询、CSV 落盘与后处理；表单参数齐全时直接执行，不再单独调用 detail、schema、list-accounts、validate。逐素材诊断只静态保留 connector code，不把详情返回的 `id` 当 connector code，不拼接或改写 `platformCode`。

只有排查连接器契约或独立单条取数时，才运行 `python scripts/rpa_dynamic_client.py detail`、`python scripts/rpa_dynamic_client.py schema`；需要选账号时运行 `python scripts/rpa_dynamic_client.py list-accounts`。从实时详情读取 `platformCode`、`inParamJsonSchemaText` / `inParamList` 与输出字段，使用 `python scripts/rpa_dynamic_client.py validate --params-json '<JSON对象>'`（长参数用 `--params-file`）校验。

详情采集统一使用受首批 CSV 门禁保护的 `run_standard_analysis.py prepare`，或 `extract_peak_frames.py --source rpa --top-materials-json <候选文件> --target-top-n <数量>`；只取一条时目标数设为 1。动态客户端的直接 `fetch`（含 `--no-download`）会在网络请求前拒绝，不能用它绕过已暂停的批次；detail/schema/list-accounts/validate 保留排查用途。

## 已落盘数据接续

用户要求“用已有数据继续分析/重做报告”时，先读指定 run 的摘要、事实包 manifest 和实际产物，缺少事实包时使用正式 `prepare-local`（参数见[分析交接契约](references/analysis-handoff.md)）；省略 `--advertiser-id` 时须传匹配的 `--rpa-shop-id`，由已有 `run-summary.json` 离线恢复广告主 ID，不重新查询授权。按[运维手册的已落盘分析接续](references/qianchuan-operations-runbook.md#已落盘分析接续)选择起点；不要默认执行 `prepare`。用户未指定来源时核对店铺、周期、素材集合和 manifest，不能只按目录日期或修改时间认定“最新”。已有信息足够则直接接续，不重复索要授权参数。

当前对话模型本身负责分析，`analysis_pending` 表示现在应继续工作，不是等待某个额外模型 API 或要求用户再说“继续”。先读取正式任务的精简事实视图、查看图片并保存观察要点，由当前模型逐项写分析片段，最后合并成一个正式 AI JSON；不得复制历史 AI JSON 的内容或证据当作模板。工具可以序列化模型已写出的文字、检查 JSON 和精确引用，不能用循环、随机标记、同义替换或通用段落生产业务结论。

**默认分析路径**：使用正式 `analysis-session --output-dir <run>`，固定按 Chapter 01、02、03 → 逐素材 Chapter 05（含逐帧分析）→ Chapter 04 汇总推进。完整读取当前 `factsPages`；每种结构首次读取 `contractPages`，引用不清时读 `referencePages` 或按需使用兼容的 references 视图。长工作项返回 `workItemPages`，先完整读取这些页；所有页不超过 8000 UTF-8 字节，`lossless-tables-v1` 首条记录说明无损解码，保留原 `model_view` 的全部事实。图片工具按 `visionBatches` 每组最多 3 张，观察后立即用 `--observations-json` 保存；最后一组可连同模型片段用 `--submission-json` 一次提交，入口完成预检、帧 checkpoint 和下一任务导航。模型使用文件工具直接写 JSON，不新增临时脚本。Chapter 04 使用完整 `reviewedVisuals` 账本及全部 frameRefs，仅有疑义时定向复看。旧 `analysis-task` 导出、分片和组装入口继续兼容。具体结构和三条命令示例见[无损读取与执行顺序](references/analysis-handoff.md#无损读取与执行顺序)。

成功提交后，事实、图片观察和片段留在磁盘；接续只读当前工作项和 `contextHandoff.resumePath` 指向的 `resume.json`，不反复加载已完成历史。宿主提供正式上下文压缩能力时可在素材提交成功边界使用，仅保留恢复指针、当前任务及未解决问题；技能不能强制清空宿主历史，也不得新建子代理转移分析。已接受片段需修改，或输入/实现指纹变化时，保留旧会话并使用分析目录内新的 `--session-dir`，不改旧 Prompt 快照。`ready_for_validation` 只是候选已组装，必须继续原 validate-analysis / 内容复核 / finalize。

## 统一分析要求

旧流程的 `frame-review`、分片组装和直接撰写 `diagnostic-ai-analysis.json` 仍可接续，均须通过相同的全帧覆盖、来源校验与内容复核；新会话使用它返回的候选路径。

统一精简分析，不设详细档位或切换参数。建议总览100–150字、经营/人群维度40–70字、两类脚本洞察每条50–80字、每张唯一画面观察25–60字、单素材核心结论80–120字；每个分类组默认一条共性判断和一个可测试方向。字数是表达预算，不是必须达到的门槛；保留事实、范围、引用及必要字段，不补字、不重复解释。

## 标准流程

### 1. 准备事实包（首次采集）

每次使用同一个 `$PWD/千川数据资源/<run-id>`，不得写入技能安装目录。下面是提交给持久执行会话的命令正文；具体工具只使用其实际支持的参数，不自行假设 300 秒短会话可承载全流程：

```bash
RUN_DIR="$PWD/千川数据资源/<run-id>"
mkdir -p "$RUN_DIR"
python scripts/run_standard_analysis.py prepare \
  --shop-id "<api_shop.extra.shop_id>" --rpa-shop-id "<rpa_shop.extra.shop_id>" \
  --start-date 2026-07-19 --end-date 2026-07-25 \
  --top-n 9 \
  --output-dir "$RUN_DIR" </dev/null >"$RUN_DIR/prepare.log" 2>&1
```

观测会话失联后先执行一次轻量 `status`：`nextAction=wait` 且 `lockState=active` 时读 `lastProgress` 并用 `python scripts/run_standard_analysis.py status --output-dir "$RUN_DIR" --wait-seconds 60` 有界观察（最多每 5 秒读一次轻量状态，离开 wait 立即返回）；`nextAction=resume` 且 `workflowStatus=interrupted` 时按授权恢复，不反复 sleep。`rpaRemainingCount` 和 `recoveryStage` 提示当前缺口与阶段，实际产物仍由恢复入口核验。标准恢复只做一次 CSV/records 与路径对账（不下载、不截帧），再由同一批处理引擎续接远程任务；已核验素材立即进入有界本地队列，与按原门禁补齐缺口的 RPA 重叠推进。有效视频和 summary/JPEG 直接复用，不重跑报表选品：

```bash
python scripts/run_standard_analysis.py resume \
  --shop-id "<api_shop.extra.shop_id>" --rpa-shop-id "<rpa_shop.extra.shop_id>" \
  --start-date 2026-07-19 --end-date 2026-07-25 \
  --top-n 9 \
  --output-dir "$PWD/千川数据资源/<run-id>"
```

用户明确要求“继续/恢复并补齐剩余素材”时可执行 `resume`：已有远端 ID 继续查询，已提交素材不重提，缺口可使用尚未提交的候选。原进程仍活动或终止状态未知时只运行 `status`；已确认终止但用户要求停止 RPA 时，仅可按运维手册执行 `--records-only` 本地对账，不执行 `resume`。

只验证报表授权、不进入 RPA 时改用 `fetch-metadata`，传入所选 `--rpa-shop-id` 以解析广告主 ID（仍写入同一类目录；只产生 `source-data/gateway-response.json` 与 `run-summary.json`，状态 `metadata_ready`）。仅当状态为 `analysis_pending` 才继续 AI。

AI 只读本次运行生成的 `diagnostic-analysis/analysis-units/analysis-unit-manifest.json`、五个章节事实包和 `diagnostic-analysis/diagnostic-analysis-prompt.md`；不得从 `top_materials.json` 组装事实，**不得手写 `missingRanks`**。历史运行可能仍有 `diagnostic-analysis/diagnostic-analysis-data.json`，但新运行不再生成且不得读取。

### 2. 视频与报告资源

标准 `prepare` / `resume` 支持 `--download-concurrency`、`--postprocess-concurrency` 两个正整数选项，默认均为 2；受限运行面可显式降为 1。恢复时可调整，不改变原业务请求与 RPA 并发 3。以 `status.workerLimits` 核对实际值。`python scripts/run_standard_analysis.py version` 返回安装代码的 `sourceSha256`，与 `run-summary.json` / `status.runtime` 中的实际执行指纹对照；旧运行无指纹时标记版本未知。

标准队列在下载完成时立即保存 MP4 路径、大小和 SHA；抽帧中断后校验复用视频及已完成 JPEG，仅补缺失或失效部分。排队尚未执行的素材不消耗重试次数，旧 `running` 尝试恢复为中断记录；中断不等于一次已完成的失败。远端收据优先于滞后的清单，已有远端 ID 只续查；有提交痕迹却没有可确认远端 ID 或结果时隔离该候选，不能猜测未提交后重发。

实际写入 RPA 产物的子进程继承执行锁，主进程死亡或自身截止时独立退出；它仍存活时不允许新运行接管。普通子进程不保留该锁。

同一运行目录，以及同一父目录下的相同业务请求均有进程互斥；active 时只观察，不重复 `resume`。锁文件采用固定文件的内核互斥，正常退出后可保留空锁文件，这不表示任务在运行；**不要手工删除锁文件**。`status.lastProgressAgeSec` 只反映检查点年龄，不单独证明卡死或可接管。

标准入口按顺序完成：**RPA 下载原始 MP4** 并持久化 `video-assets/<materialId>.mp4` → **原始视频提取高光帧**（以原始 MP4 提取 `zero_second` 与局部峰值 JPEG）→ 报告直接复用该 MP4，不再执行第二次 FFmpeg。`finalize` 优先硬链接到 `final-report/assets/videos/<materialId>.mp4`，跨文件系统或权限不允许时才回退为字节拷贝；HTML/JSON 只引用相对本地路径，图片不依赖三方链接。交付后默认保留源视频；只有已显式生成并校验 ZIP 时才允许 `cleanup-videos --mode delete`。任一引用资源失败都不能进入 `complete`。

### 3. 当前模型分析与统一交付

Chapter 01 的 `portfolio.hotMaterialRule` 若存在，只能据其阈值、命中数量、素材指标和 `evidenceRef` 输出 `hotMaterialInsight`；Presentation 将其并入既有 AI 区域，不重复渲染爆款卡片。

#### AI 交接前置检查

按素材串行领取任务，所有不同 SHA 的成功 JPEG 必须由当前模型实际查看；相同 SHA 只实际查看一次。新账本每批不超过 6 帧（按唯一 SHA 计），每批结束只写一次完成 checkpoint；组内最多三张图的观察暂存不代表整批完成。

先由 analysis-session 按 manifest 校验五包与 JPEG，再读本轮冻结提示词一次并完整读取当前任务页；无需重复读取完整榜单和曲线。原五包继续用于报告与验证；输出截断不算已读取，不自行 head 截取事实。核对字节数、SHA-256、素材数、成功帧数、`missingFrames`。会话复用正式 `analysis-input-index.json` 与帧账本，新建账本每批最多 6 个唯一 SHA，旧 4 帧账本按冻结批次续接；这不是图片工具的单次张数。按返回的 `visionBatches` 每组最多 3 张，记录少量真实可见要点后立即暂存，恢复只看剩余 SHA，不等待整素材观察留在内存。已全缓存的素材无需再看图，所有 frameRef 仍完整保留，商业解释按素材分别核对。相同提交和已存观察允许幂等重试；指纹或图片变化必须停止，不混用新旧输入。策略或 guardrail 拒绝时立即**冻结并恢复帧审阅**，不得跳过任何帧或改用其他产物；细节见[运维手册](references/qianchuan-operations-runbook.md#图片理解断点与吞吐)。

“唯一正式 JSON”约束交付一致性，不限制当前模型分批撰写。可直接写完整 JSON，或使用正式 `assemble-analysis` 合并模型已写出的片段；不得使用外部模型、`codex exec`、子进程或子代理替代当前模型分析。最终使用 `schemaVersion: 5`。保留经营表现、人群、脚本、高光画面和互动时序边界；第 01 只读由 `chapter01-portfolio-input.json` 的 `portfolio` 全量事实投影的正式任务，不读顶层 `materials`/`materialIds`、`topMaterialRoi`、RPA 精选或 Chapter 05 回退数据。`summaryMetrics` 使用当前 ROI（`total_prepay_and_pay_order_roi2`），不含 `netSettlementRoi`/`overallPaymentRoi`；`overallConclusion` 为第一部分唯一直接展示段落（建议 100–150 字，突出核心判断和优先动作），`commercial.conclusion` 只补充业务含义。第 `04｜高光画面洞察` 输出 `highlightDashboard`，每个成功帧按 `portrait`、`product`、`other` 固定顺序归类一次且仅一次；不得把帧秒数宣称为具体话术。

以该提示词为 AI JSON 输出契约，按其字段、证据、覆盖和枚举规则生成结果：每个成功 JPEG 都有分析，`missingFrames` 写入 `frameCoverageNote`；引用逐字复用事实包标识。`preferencesByMetric` 仅供 drill-down，AI 不读取其 TOP3 字段。v4 仅用于历史报告兼容。`keywordCloudInsight.oneSentenceConclusion` 建议为 28-45 个中文字符的单一核心判断；summary 控制在 60-110 个中文字符作为表达建议，证据不足可更短，验证动作放入 summary 或 sellingPoints；不得重复关键词频次、卖点卡片和验证动作。

逐帧分析必须来自实际 JPEG；只描述真实主体、动作、关系与信息层，不强求独有变化、叙事作用或每帧商业假设。同图允许相同观察，保留所有 frameRef。商业解释允许证据不足状态和同素材同图的共享引用，详见[分析交接契约](references/analysis-handoff.md)。正文长度随证据丰富程度变化；分类和分点数量遵循 Chapter 04 的 3–5 类、默认各一条（结构兼容 1–3 条）及真实不足复核契约，不能凑数。

报告执行不得新增临时脚本，也不得以内联代码批量生产正文。需要修改技能程序时须有明确技能开发授权；当前报告流程只使用维护中的正式命令。具体写入边界、复核记录和重试规则见[分析交接契约](references/analysis-handoff.md)。

会话返回 `ready_for_validation` 后，对其 `analysisJson` 路径先校验（保存候选同名 `.validation.json`，不修改事实或运行状态）；首次可同时生成待复核模板：

```bash
python scripts/run_standard_analysis.py validate-analysis \
  --output-dir "$PWD/千川数据资源/<run-id>" \
  --analysis-json "<analysis-session 返回的 analysisJson>" \
  --review-template "$PWD/千川数据资源/<run-id>/diagnostic-analysis/quality-review.json"
```

失败先按运维手册区分状态、证据引用、结构、内容和资源问题，再由当前模型修正有证据支持的报错字段；用户要求全新分析时，写新候选文件，不改旧 JSON。不得用临时脚本填长文、删必填分类或绕过质量门禁。没有新证据的同类错误不做机械循环，不重跑 `prepare`；结构或引用错误必须修正；相似度命中由当前模型基于证据写复核记录；字数仅提示，不用补长。结构通过但需内容复核时，程序模板已复制全部引用和绑定哈希；当前模型核对证据，仅填写 decision/reason，不能默认放行。模板不覆盖已有文件，后续不再传 `--review-template`。填完后直接执行 `finalize --quality-review <文件>`，由它重新执行同一门禁；无需先重复一次成功的 validate-analysis。有硬错误先修正并重新校验。

### 4. 交付

```bash
python scripts/run_standard_analysis.py finalize \
  --output-dir "$PWD/千川数据资源/<run-id>" \
  --analysis-json "<analysis-session 返回的 analysisJson>"
```

仅当 `run-summary.json` 为 `complete` 才交付独立报告目录 `final-report/`，主入口为 `final-report/final-report.html`。图片和视频均为本地资源；不得交付草稿报告；封面只展示业务主体；结构校验和内容复核信息保留在机器 JSON 中，不插入封面。默认生成高光帧 XLSX，不生成 ZIP；`--include-workbook` 保留兼容，用户明确不需要 Excel 才使用 `--skip-workbook`，需要 ZIP 时传 `--archive`。完成后同一目录可再次执行 `finalize --include-workbook` 补齐 Excel，仍需原候选 JSON、复核记录和事实包；不会重采 RPA。交付前核验 `highlightExportHref` 指向的工作簿存在，图片损坏导致导出失败须修复，不能静默隐藏入口。仅无成功帧或显式省略时说明没有 Excel。报告使用 `assets/report/`，`finalize` 自动内联资源，不得手工修改或改写成品 HTML。`report-build-receipt.json` 对 `reportData → workbook(可省略) → html → archive(可省略)` 分阶段记账，输入哈希不变时重跑只续接未完成阶段。若仍为 `analysis_pending`，修正 AI JSON 后，直接重新执行 finalize，无需重新执行 prepare 或任何运行命令；若为 `running/report` 且旧进程已结束，同样直接重跑 finalize 接管陈旧锁，无需手工改 `run-summary.json`。源 MP4 默认保留；删除前必须先用 `--archive` 生成并校验 ZIP。其余故障按运维手册处理。

## 峰值帧口径

每条素材保留第 0 秒封面帧 `zero_second`，从 `CLICK_COUNT` 的全部局部峰值按点击数降序、秒数升序取最多五帧；少于五个不伪造。后处理成功要求第 0 秒帧成功，且存在局部峰值时至少一张峰值帧成功。
