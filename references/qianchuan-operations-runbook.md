# 千川素材分析运维手册

首次采集从 `scripts/run_standard_analysis.py prepare` 开始；用户要求复用落盘数据时，直接按“已落盘分析接续”处理。失败排障与接续均以经过质量校验的 `finalize` 收口。

## 运行目录

标准运行目录固定为 `$PWD/千川数据资源/<run-id>`。平台的 `$PWD` 是当前对话工作空间根目录；本地从技能仓库执行时，`$PWD` 是仓库根目录。`prepare`、`status`、AI 产物、`finalize` 和所有恢复命令必须复用同一个运行目录。本手册中的 `/path/to/run` 均指已创建的 `$PWD/千川数据资源/<run-id>`，不得改为技能安装目录。

在启动 AI 分析前，先运行：

```bash
python scripts/run_standard_analysis.py status --output-dir "/path/to/run"
```

仅当状态为 `analysis_pending`，并且五个章节事实包、manifest 与 `diagnostic-analysis-prompt.md` 已由成功的 `prepare` 或上述可追溯本地构建核验生成时，才可继续。历史运行可能仍有 `diagnostic-analysis-data.json`，但新运行不再生成；不得从该快照、`top_materials.json` 或手写 `missingRanks` 组装事实包。

## 已落盘分析接续

先确定当前代码版本、输入 run 与本轮输出位置，记录 manifest、五个章节事实包、提示词与 JPEG 的 SHA-256。不要用 mtime、目录名或旧报告的 `complete` 推断数据版本或本次分析成功。正式本地入口是 `prepare-local`，参数与片段合并、分层复核见[分析交接契约](analysis-handoff.md)。CLI 没有 `recover-local`、`--csv`、`--source-file`，不要把设计文档里的命令当成已实现接口。

| 实际产物与状态 | 接续动作 |
| --- | --- |
| 五章事实包、提示词、JPEG 齐全且 `analysis_pending` | 当前模型直接进行帧审阅与分析，不调用 API/RPA/下载/FFmpeg |
| 数据齐全，但旧结果已 `complete` 或曾绕过门禁 | 先保留旧摘要、AI JSON、报告和 ZIP；核对 request、素材集合、manifest 和资源后，在隔离副本或已有备份的 run 中恢复 `analysis_pending`，生成新候选 JSON；不能把单改状态当成修复成功 |
| RPA/视频/帧完整，但五章输入缺失或与状态不一致 | 使用正式 `prepare-local`，显式传入本批次已确认请求参数与运行目录；同时保留 Chapter 01 全量榜单来源，不能拿精选 TopN 代替全量榜单。无法确认输入来源时精确报告缺口，不重跑在线 prepare 来掩盖问题 |
| 只有列表 CSV，没有诊断 records/JPEG | 尚不属于“RPA 已落盘”；说明缺少的阶段，不能把列表 CSV 当作完整诊断事实 |

`prepare-local` 封装本地事实构建与状态接续，拒绝覆盖现有事实包或 complete 运行。不再手工调用私有函数写摘要。`repair_manifest.py` 的 `rpaCalls=0` 仅证明不提交 RPA，仍可能触发下载/截帧；用户只授权分析、或图片视频已经齐全时不要调用它。

用户要求使用新提示词时，核对仓库 `prompts/diagnostic-analysis-prompt-v1.md` 与 run 内提示词的哈希；二者不同不等于事实包坏了。在本轮隔离分析目录中保存选定提示词与版本信息，不能默默覆盖旧运行快照。候选 AI JSON 应放在与本轮 `analysis-units/` 同级的 `diagnostic-analysis/` 下（例如 `diagnostic-ai-analysis.retry-<id>.json`），因为正式校验从这个相对位置加载 Chapter 01；不要随意写到 `/tmp` 后直接 finalize。

### AI 交接与失败定位

- 当前对话模型就是分析执行者，`analysis_pending` 不需要另一个模型 API。正式 JSON 合并之前，可以多轮读文件、看图、记录要点并逐项撰写分析片段；不要以“没有模型调用接口”结束任务。
- 通过 analysis-task 读取正式精简任务：Chapter 01 的聚合保留全量口径；Chapter 05 按素材保留脚本、帧和峰值，曲线points仍完整用于报告。记录已读页、剩余页和哈希；不反复打印同一大包，不自行截取事实。
- 帧账本中的实际标识来自 `frames[*].evidenceRef`，不是不存在的 `frameRef` 输入字段。逐帧记录 frameRef、framePath、图像 SHA-256、少量实际观察和状态；仅有 completed ID、没有可恢复笔记不算可复用的审阅结果。
- 原文件哈希一致且笔记完整时续接未完成部分；文件变化或笔记丢失则重新审阅受影响帧。同字节图像允许复用视觉要点，但保留所有素材/帧的覆盖记录，核对每条素材各自的上下文。相同图片的来源正确性仍需核对本地 summary、视频和抽帧记录，不能从重复图片推出素材业务一致。
- 新生成的 JSON 不继承旧运行的内容、引用或分类；机器只可序列化模型生成内容、合并已经逐项确认的字段及校验格式，不可循环拼经营结论、添加通用尾句或删除难通过的字段。

| 校验结果 | 处理方式 |
| --- | --- |
| `run is not awaiting AI analysis` | 检查摘要和实际产物，按上述恢复规则处理；不归因于事实内容 |
| unknown/illegal evidence reference | 回到对应章节精确查找合法引用；禁止从旧 JSON 复制或仅字符串替换 ID |
| 缺字段、枚举、覆盖错误 | 保持事实不动，由当前模型补齐本次输出结构与遗漏项 |
| 文本相似或长度提示 | 相似度进入当前模型证据复核，字数仅作 advice；套话必须重写，不通过脚本补字数 |
| 相同图片的重复观察 | 本地文件 SHA-256 一致时，仅视觉观察重复可以豁免；同素材同图可使用 sharedInterpretationRef；其他商业解释需证据复核，分组、引用与覆盖照常检查 |
| 疑似相似度误判/事实无法支持额外解释 | 保存候选 JSON、冲突字段和真实证据，用最小样本定位；不降低阈值、不改图、不随机改写求通过 |
| 工具策略拒绝/任务中断 | 单列工具状态，按断点恢复；不能冒充 RPA、事实包或质量门禁失败 |

每次校验保留实际命令、候选 JSON 哈希、退出码和错误字段。失败后只有新增事实核验或实质修正才重试；没有变化时停止机械重试并报告具体冲突。通过校验后对同一候选文件 finalize；不能调用底层渲染器的 `require_ai_quality=false`，也不能把已有旧 HTML/ZIP 当作本轮交付。

## 长任务元数据

通过长时执行工具提交 `prepare` 时，设置 `max_runtime_seconds: 3600`、`completion_mode: data_collection`、`success_pattern: "run_summary=.*run-summary\\.json"`，并以本次 `run-summary.json` 的状态作为 `verify_command`。这些是工具元数据，不是 `prepare` 的命令行参数；只有验证为 `analysis_pending` 才可进入 AI 分析。

长任务每次 manifest checkpoint 还会追加一条 `peak-frames/batch-progress.jsonl`，只保留时间、runId、`rpaCollection`、`stageCounts` 和中断信息。运行面会话失联时可直接读末行，无需回显进程命令行或猜测目录数。

观测轮询每次等待不超过 60 秒，每次都使用新的有界 `status` 调用，使用者的停止指令可以在下一个轮询周期被处理。`status` 是轻量只读入口，不加载报告、Jieba、OpenPyXL 或 Pillow。一旦运行面会话为 `lost` 或控制通道丢失，立即向用户说明“控制面异常，后台状态待核对”，并只执行一次 `status`：锁为 active 时才说明后台仍在运行并反读 `lastProgress`；锁为 stale/missing 且 `workflowStatus=interrupted` 时直接进入标准恢复。不得在核验前固定声称后台仍在运行。

## 策略拒绝但后台仍在运行

`GUARDRAIL_ERROR`、策略拒绝或连接器拒绝只能说明调用层没有给出可用完成态，不能据此判断 `prepare` 未提交或已失败。先通过运行面的后台任务/执行会话查询该命令的真实状态；查询不可用时，再反读 `run-summary.json`、`peak-frames/batch-manifest.json` 和 CSV/records/summary 落盘数量。后台为 `running` 时不得重复提交、覆盖目录或向用户报告任务中止；保持同一运行目录，等待后台终态。后台为 `analysis_pending` 时直接进入 AI 分析，为 `complete` 时仅核验交付物。

长任务的工具返回与真实提交是两个字段：调用层拒绝但无法确认后台是否接收时，记录 `tool_call_blocked`、`submitted_unverified` 和 `needs_confirmation`，表示提交结果未知；不得在未知状态重提 prepare，也不得在未知状态将任务改写为 `failed`。只有后台明确终止、退出码可见，且 `run-summary.json`、锁文件和产物链路共同证明未完成时，才允许进入失败恢复。

连续策略拒绝时只做一次有目的的后台/运行摘要核查；之后停止同类探查调用，保留最后一次成功观测、拒绝时间、runtime session/task 标识、运行目录和锁文件。只读核验恢复后产生的状态和产物，再决定接续或恢复；在此之前对用户使用“后台状态未知/待确认”，不使用“进程已中止”或“prepare 失败”。

图片理解工具首次被策略拒绝时，将 AI 阶段记录为 `frame_review_pending`，保存已完成帧批次、未完成帧批次和事实包路径；不得继续拆分批次、逐帧重试或改用 bash/搜索/文件读取工具绕过策略。工具恢复后只从未完成批次续接，已完成批次不得重复查看；若恢复前无法核验，只能报告 AI 阶段待恢复，不得报告事实包或 prepare 失败。

### 图片理解断点与吞吐

首次打开 JPEG 前执行：

```bash
python scripts/run_standard_analysis.py frame-review --output-dir "$PWD/千川数据资源/<run-id>"
```

该命令冻结 `analysis-input-index.json`（schemaVersion=2），核对五章事实 manifest、Prompt、每个 `materialId`、`frameRef`、`framePath` 和实际 JPEG SHA-256；返回当前素材、整批 `frameRefs` 和去重后的 `imagesToView`。实际标识来自事实包 `frames[*].evidenceRef`，不是事实包中不存在的 `frameRef` 字段。按素材串行，新建账本每批最多 6 个唯一 SHA（旧账本按原冻结批次续接）；同批先连续/并发打开全部待查看图片，不跨素材混合商业归因。返回的 `cachedObservations` 提供当前批次可复用的笔记。相同 SHA 只实际查看一次，后续引用复用纯视觉观察，不复用商业解释或相邻帧变化判断。

当前模型用文件编辑工具写一个批次观察 JSON（以下为结构示例，不是业务事实）：

```json
{
  "inputIndexSha256": "命令返回的索引哈希",
  "frameRefs": ["命令返回的当前批次全部引用"],
  "observations": [
    {"sourceFrameRef": "imagesToView 中的 frameRef", "visualObservation": "当前模型实际看到的少量要点"}
  ]
}
```

只为 `imagesToView` 的唯一 SHA 提供观察；全部命中缓存时 `observations=[]`。观察只记录真实主体、动作/状态和信息层；相邻变化和商业解释在该素材的分析正文里逐项核对。然后提交一次 checkpoint：

```bash
python scripts/run_standard_analysis.py frame-review \
  --output-dir "$PWD/千川数据资源/<run-id>" \
  --batch-json "$PWD/千川数据资源/<run-id>/diagnostic-analysis/frame-batch-01.json"
```

程序核验全批观察后仅原子写一次 `frame-review-progress.json`，维护 `visualObservationCache`、`frameRefsBySha256`、`reviewBatchCount`、completed/pending 和 checkpoint 的 `viewedImageCount`/`cacheHitCount`。未完成批次不写完成态。全部引用完成自动写 `status=complete`、`completedAt`；validate-analysis 和 finalize 对新运行校验此状态。SHA/笔记只能支持复用，不能单独证明模型确实看过图，仍须保留实际工具日志。

中断后重跑无 batch-json 的命令即可返回下一批，已完成批次不重复查看。输入变化时命令拒绝继续；显式 `--rebuild` 会备份旧索引/账本，重新冻结，仅保留源引用、路径和 SHA 都未变化的有效视觉观察，其余重审。历史旧账本不会被猜测为已完成，需要显式重建。不得再经 Bash 二次摘录图片理解结果，不逐帧 patch 账本。图片策略拒绝仍冻结并等待恢复，不得以压缩、改读文本或跳帧绕过。

若进程被强制终止留下 stale `.frame-review.lock` 或 `.video-cleanup.lock`，先根据锁内 pid、hostname、processStart 确认原进程已结束，仅移除该锁后重试对应命令；active 锁不可移除。保留事实包、索引和账本原样，不能靠清空账本恢复。

只有后台明确终止且运行摘要或产物链路也显示未完成，才按本手册的“运行中断或陈旧批次”处理。策略服务未提供原始拒绝原因时，记录为 `tool_call_blocked` 与“待策略侧日志确认”；不要把业务任务、RPA 或 FFmpeg 归因为策略故障。

## 运行中断或陈旧批次

先读取运行目录的 `run-summary.json` 和 `peak-frames/batch-manifest.json`。若 manifest 中仍有 `running` 或 `submitted` 状态，不能直接覆盖目录。恢复已有批次时，默认先做不触发 RPA 的本地修复；`--resume` 只轮询已保存的远程任务，不会重新提交 RPA：

`status` 会核对工作流锁和批次锁的 pid、hostname 与 processStart。持久摘要仍为 `running`、但两把锁都不是 active 时，对外返回 `workflowStatus=interrupted`、`persistedWorkflowStatus=running` 和 `recoveryRequired=true`；`status` 本身保持只读。不得使用 `pgrep -af` 或其他会输出完整命令行与环境变量的命令查进程；需要复核时只按锁内 pid 查存活性，不输出 args/env。

需要继续核对已提交任务时，运行标准恢复入口：

```bash
python scripts/run_standard_analysis.py resume \
  --shop-id "<授权店铺 ID>" --start-date 2026-07-19 --end-date 2026-07-25 \
  --top-n 9 --advertiser-id "1770096415226951" \
  --output-dir "/path/to/run"
```

`resume` 校验请求参数与旧摘要一致，拒绝 active 批次，自动接管陈旧工作流/批次锁，先执行 `rpaCalls=0` 的本地修复；本地阶段在同一次 `resume` 内按每批 2 条自动循环，直到全部排空或某批失败/无进展，再用已保存收据查询原远程任务并补齐缺口。它不重跑报表选品，也不需要人工重复调用 5 轮。

先运行 `run_standard_analysis.py status`，按五层产物分别盘点并记录数量：`rpaRequestCount`（提交数）→ `rpaAcceptedCount`（有效诊断结果数）→ `rpaSelectedCount`（含可用 videoUrl 的选中数）→ `recordsPersistedCount`/CSV 数（诊断结果已落盘数）→ `postprocessSucceededCount`（截帧与 `summary.json`、`summary.md` 均完整数）。提交数、平台/UI 的“成功”数字、素材目录数均不等于本地落盘或完整后处理数；没有本地 CSV/records 时不得声称平台已成功或失败。

调度使用分阶段屏障：先按 RPA 并发 3 个逐波收集，当前波次的 RPA 和 CSV/records 落盘全部终态后即可进入下一波；达到目标 records 数前不启动视频下载或 FFmpeg。本地后处理默认视频下载并发 2、FFmpeg 并发 2；下载与截帧使用独立信号量。后处理终态失败时，才对下一顺位候选素材执行独立的 RPA 补位阶段。

若用户未明确授权再次调用 RPA，先运行“修复保留产物”章节的 `repair_manifest.py`。该修复优先核验 `rpa-receipts/<materialId>.json` 的 `records_persisted` 收据；收据中的 records 已包含目标素材的平铺 `recordType=material_info` 与非空 `videoUrl` 时，即使 manifest 仍为 submitted/pending，也直接进入本地后处理，不会发起 RPA。即使 manifest 的 `postprocessStatus` 已写成 `succeeded`，也会以实际 `summary.json`、`summary.md` 和 JPEG 是否完整为准，补跑缺失的截帧。检查 `repair-manifest.json` 的 `rpaCalls` 必须为 `0`，并核对修复后的三层数量。

`resume` 返回的本地修复或远程对账需要进一步诊断时，才使用下面的底层命令：

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --top-materials-json "/path/to/run/top_materials.json" \
  --target-top-n 9 \
  --candidate-top-n 27 \
  --params-json '{"date_type":"CUSTOM","custom_start_date":"2026-07-19","custom_end_date":"2026-07-25"}' \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --output-dir "/path/to/run/peak-frames" \
  --resume
```

`--resume` 只可用于同一日期、同一候选池和同一店铺的既有批次；它会复用有效 summary 与已落盘 records。

`--resume` 只对带已保存 `task_group_id` 的 `submitted` 素材继续轮询同一远程任务。没有本地 CSV/records 且没有可复用远程任务 ID 的素材直接标记失败，不会重新提交 RPA。用户要求停止 RPA 时只使用上面的本地 repair，并把悬空素材保留为待对账。

## 修复保留产物

当 CSV、records、summary 或 manifest 不一致时，执行：

```bash
python scripts/repair_manifest.py \
  --output-dir "/path/to/run/peak-frames" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --diagnosis-csv-dir "/path/to/run/diagnosis-csv" \
  --download-concurrency 2 \
  --postprocess-concurrency 2 \
  --max-materials-per-run 2
```

该命令先执行 `records_persisted receipt -> records`，再执行 `CSV -> records`、`records -> 后处理` 和 `summary/JPEG -> manifest` 修复，不重新调用 RPA。下载与 FFmpeg 分别受独立并发上限控制；独立 `repair_manifest.py` 默认每次只启动 2 条素材，视频下载并完成大小/SHA-256 后立即 checkpoint，整条素材完成后再次 checkpoint。读取 `repair-manifest.json.postprocessRemainingCount`，大于 0 时可继续执行同一底层命令；标准 `resume` 已内置该有界循环。不要再用外部 `timeout` 在任意时刻杀进程。若 manifest 缺失，再传入 `--top-materials-json`；无法证明诊断阶段时保持 `rpaStatus: unknown`。`postprocessAttemptCount` 是公平调度计数，`postprocessAttempts` 始终是尝试记录列表；旧错误版本留下的整数会自动迁移到计数字段。

## 报告生成中断与续跑

`finalize` 按 `reportData → workbook → html → archive` 写入 `final-report/report-build-receipt.json`。收据绑定候选分析、质量复核、冻结输入索引、records/summary 和渲染代码哈希；输入未变化且阶段产物存在时，重跑只续接未完成阶段。不要删除已经完成的 1.2GB 视频资产或手工把摘要改回 `analysis_pending`。

高光帧 XLSX 使用最长边 112px、JPEG quality 72 的临时缩略图；资产表和图片墙分别锚定缩略图，不把完整 JPEG 重复嵌入工作簿。完整 JPEG 仍复制到 HTML 报告资产，不降低网页证据质量。若 workbook 阶段被 SIGKILL，确认旧进程结束后直接重跑同一 `finalize`；已写好的 report data、presentation 和视频资产会复用。

检查保留的 `diagnosis-records/`、`diagnosis-csv/`、素材 `summary.json` 与 JPEG，而不是仅凭文件名判断成功。RPA 输出字段、下载上限和波次状态的完整约束见 [RPA 输出契约](rpa-output-contract.md)。


## 交付后源视频清理

finalize 在报告和 ZIP 校验通过、摘要为 complete 后默认保留 `peak-frames/video-assets/<materialId>.mp4`，避免把大文件清理校验并入交付关键路径。需要回收空间时，在交付完成后独立执行下方 `cleanup-videos --mode dry-run/delete`；也可在 finalize 显式传 `--video-cleanup delete`，但长任务环境不推荐把清理与交付绑在同一进程。

清理前核验每个原始/预览路径、大小、SHA，预览须 FFmpeg 解码通过、HTML 引用本地预览、ZIP 的必需条目完整且归档内容与磁盘字节一致；禁止符号链接和目录级删除。任一前置校验失败不删除，单文件删除失败记 partial。未列入 manifest 的文件保留。

```bash
python scripts/run_standard_analysis.py cleanup-videos --output-dir "$PWD/千川数据资源/<run-id>" --mode dry-run
python scripts/run_standard_analysis.py cleanup-videos --output-dir "$PWD/千川数据资源/<run-id>" --mode delete
```

`final-report/video-resource-cleanup.json` 在删除前保存 planned 清单，逐个反读并记账，支持失败重试和中断恢复。清单绑定已验证 ZIP 哈希，因此是 ZIP 外的独立审计附件，不将它回打包进该 ZIP。`run-summary.json.videoResourceCleanup` 单列 status、releasedBytes 和 rerenderRequiresSourceRecovery；清理失败不回退 complete 报告到 analysis_pending，不得重跑分析或渲染来重试清理。重复清理不重复累计释放字节。已清理运行若需重新抽帧/渲染，应先恢复源视频；不能用预览冒充原始输入。

## Chapter 04 分类过粗或条目过少

先比对候选 AI JSON 与 presentation-report.json：若数量相同，修正模型分析，不改前端截取条数。新 Prompt（mechanism-depth-v1）要求本批次默认 3–5 个主导画面机制类别；标准分类/本批次分类每个非空组默认一条共性结论和一个裂变方向，结构接受 1–3 条。54 帧归入一个混合总类仍须审查真实机制边界；每部分各一条无需虚构证据不足。

分类不足填写 classificationLimitation，组内结论/方向不足填写 contentLimitation；说明不足后仍需 evidence_limited 复核，不能以“缺少因果证明”为由忽略可见画面差异。数量门槛通过后继续检查语义边界和可测试变量，不用脚本拆类或复制条目凑数。新规则由冻结 Prompt 启用；重做历史输入时创建新候选并重新绑定索引和复核哈希，不覆盖历史成品。
