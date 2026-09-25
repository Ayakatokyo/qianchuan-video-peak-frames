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
- 默认通过 analysis-session 领取/恢复当前任务，顺序为 Chapter 01、02、03 → 逐素材 → Chapter 04。完整读当前 factsPages，每种结构首次读 contractPages，引用按需读 referencePages；若返回 workItemPages，先完整读取工作项分页。所有页不超过 8000 UTF-8 字节，lossless-tables-v1 首条 record 给出无损解码说明，保留原 model_view 全部事实。旧 analysis-task 导出仍兼容，不反复导出已完成任务。
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

仅在运行面的长时执行工具确实支持以下字段时，通过它提交 `prepare` 并设置 `max_runtime_seconds: 3600`、`completion_mode: data_collection`、`success_pattern: "run_summary=.*run-summary\\.json"`，并以本次 `run-summary.json` 的状态作为 `verify_command`。这些是工具元数据，不是 `prepare` 的命令行参数；只有验证为 `analysis_pending` 才可进入 AI 分析。

长任务每次 manifest checkpoint 还会追加一条 `peak-frames/batch-progress.jsonl`，只保留时间、runId、`rpaCollection`、`stageCounts` 和中断信息。运行面会话失联时可直接读末行，无需回显进程命令行或猜测目录数。

长任务优先使用运行面已有的持久执行会话；只有实际验证跨工具调用存活时，才通过 `setsid ... </dev/null >"$RUN_DIR/prepare.log" 2>&1 &` 脱离 Bash 会话。setsid 只改变进程会话关系，不能抵抗运行面回收或 SIGKILL；一次成功不能证明所有运行都保活。`status` 是轻量只读入口，不加载报告、Jieba、OpenPyXL 或 Pillow。日常进度以 `batch-progress.jsonl` 和最后一次 `status` 为准；不为了“看进度”连续执行多次 Bash、`jq`、`head` 或短 `sleep`。一旦运行面会话为 `lost` 或控制通道丢失，立即向用户说明“控制面异常，后台状态待核对”，并只执行一次 `status`：锁为 active 时才说明后台仍在运行并反读 `lastProgress`；锁为 stale/missing 且 `workflowStatus=interrupted` 时直接进入标准恢复。不得在核验前固定声称后台仍在运行。

优先使用 `status.nextAction`：`wait` 才执行 `python scripts/run_standard_analysis.py status --output-dir "$RUN_DIR" --wait-seconds 60` 有界观察，工具超时须大于该等待窗口；每 5 秒检查，退出 wait 即返回，不再盲等 240/280/1500 秒。`resume` 表示中断待按授权恢复，`finalize` 表示从报告构建断点恢复，`analyze` 表示进入当前模型分析，`verify_delivery` 表示核验交付。`recoveryStage` 与 `rpaRemainingCount` 是基于落盘计数的阶段提示，不代替文件校验。进程已中断时不得以反复 sleep 等待其自行完成；工具返回 `-1`/`-9` 或极短 duration 也不证明后台继续运行或真实阶段耗时。常规观测不再组合多轮 find/jq/进程扫描。

## 策略拒绝但后台仍在运行

`GUARDRAIL_ERROR`、策略拒绝或连接器拒绝只能说明调用层没有给出可用完成态，不能据此判断 `prepare` 未提交或已失败。先通过运行面的后台任务/执行会话查询该命令的真实状态；查询不可用时，再反读 `run-summary.json`、`peak-frames/batch-manifest.json` 和 CSV/records/summary 落盘数量。后台为 `running` 时不得重复提交、覆盖目录或向用户报告任务中止；保持同一运行目录，等待后台终态。后台为 `analysis_pending` 时直接进入 AI 分析，为 `complete` 时仅核验交付物。

长任务的工具返回与真实提交是两个字段：调用层拒绝但无法确认后台是否接收时，记录 `tool_call_blocked`、`submitted_unverified` 和 `needs_confirmation`，表示提交结果未知；不得在未知状态重提 prepare，也不得在未知状态将任务改写为 `failed`。只有后台明确终止、退出码可见，且 `run-summary.json`、锁文件和产物链路共同证明未完成时，才允许进入失败恢复。

连续策略拒绝时只做一次有目的的后台/运行摘要核查；之后停止同类探查调用，保留最后一次成功观测、拒绝时间、runtime session/task 标识、运行目录和锁文件。只读核验恢复后产生的状态和产物，再决定接续或恢复；在此之前对用户使用“后台状态未知/待确认”，不使用“进程已中止”或“prepare 失败”。

图片理解工具首次被策略拒绝时，将 AI 阶段记录为 `frame_review_pending`，保存已完成帧批次、未完成帧批次和事实包路径；不得继续拆分批次、逐帧重试或改用 bash/搜索/文件读取工具绕过策略。工具恢复后只从未完成批次续接，已完成批次不得重复查看；若恢复前无法核验，只能报告 AI 阶段待恢复，不得报告事实包或 prepare 失败。

### 图片理解断点与吞吐

`frame-review-progress.json` 的 `visualObservationCache` 保存唯一图观察，`frameRefsBySha256` 保存全部已完成帧的 SHA 映射；暂存不增加完成引用，只有全批核验通过才更新覆盖。

正式入口已返回当前任务、图片组和提交模板，不得再经 Bash 二次摘录；过长响应按 workItemPages 完整读取，不用 head/tail 丢掉图片或后续页。

默认由正式 `analysis-session` 管理帧审阅、片段提交和下一任务导航，三个调用示例与提交结构见[分析交接契约](analysis-handoff.md#无损读取与执行顺序)。会话沿用 `analysis-input-index.json`（schemaVersion=2），冻结五章事实 manifest、Prompt、每个 materialId、frameRef、framePath 和实际 JPEG SHA-256。实际引用来自事实包的 `frames[*].evidenceRef`，不猜字段或自造引用。

`frameReview.visionBatches` 每组最多 3 张唯一 JPEG，按组实际看图，立即用该组 `stashTemplate` 填写少量观察并通过 `--observations-json` 保存；不要先看完整批再把观察留在内存。新账本每批最多 6 个唯一 SHA，旧 4 帧账本按冻结批次兼容续接；账本批次和图片工具分组不能混淆。原图、所有成功帧与 frameRef 完整保留，不降采样。

观察 JSON 固定包含 `inputIndexSha256`、当前账本批次全部 `frameRefs`、`observations`；observations 只写本组实际看的 SHA，每条包含 `sourceFrameRef` 和非空 `visualObservation`，不能提交模板 null。当前模型用文件工具写 JSON，不执行临时脚本。部分 stash 原子保存到 `visualObservationCache`，未看的帧仍待处理；后续工作项只给剩余 imagesToView，cachedObservations 直接复用，不再从 Bash 摘录图片工具长篇返回或运行 find/sha256sum 重建去重表。

最后一组观察可随提交外壳的 `frameObservations` 一并送入 `--submission-json`，外壳必须有当前 `inputSha256/taskId/part`；part 由模型直接撰写。入口执行片段预检、所需帧 checkpoint 和下一任务导航；缺图/结构错误停留当前任务，不把部分保存误作完成。全缓存素材无需再看图，仍须根据各自人群、脚本、互动和经营事实写商业解释。相同 submission、已保存 stash 可幂等重试，不改写已接受内容。

正常中断只重跑不带提交参数的 analysis-session，读取当前工作项与 `contextHandoff.resumePath` 指向的 `resume.json`，已完成事实、观察、片段都留磁盘。宿主提供正式上下文压缩能力时可在素材提交成功后使用，仅保留恢复指针、当前任务及未解决问题；技能无法强制清空宿主历史，不能新建子代理绕开单 Agent 边界。任务 elapsedSeconds 包含空闲和等待，不是模型推理耗时。

输入/实现指纹变化、图片被替换或已接受片段被修改时拒绝继续。需要实质改写已接受片段，用 `--session-dir <diagnostic-analysis 内新目录的绝对路径>` 保留旧会话另起；事实/Prompt/JPEG 变化须先在隔离副本核验并显式重建索引，不覆盖旧提示词快照。Chapter 04 使用 `reviewedVisuals` 的完整 SHA 观察与全部 frameRefs，不重复看完整图集；机制不清或复核命中时定向回看。`ready_for_validation` 只表示候选已组装，必须用返回的 analysisJson 继续 validate-analysis、内容复核和 finalize。

独立旧流程仍支持 `frame-review` 查询当前批次、`--stash-json <观察文件>` 部分暂存、`--batch-json <checkpoint文件>` 完成整批；全部引用最终须为 status=complete 且有 completedAt。索引变化时显式 `--rebuild` 会保存旧索引/账本，只复用来源引用、路径、SHA 均未变的有效观察。旧四帧账本不需因升级而重看；无法验证的历史笔记不能猜测成已完成。SHA/笔记只支持复用，实际看图仍由真实工具日志证明。

策略或 guardrail 拒绝时冻结帧审阅并等待恢复，不继续拆图重试、压缩、换工具或跳帧绕过。正常退出后 `.analysis-session.lock`、`.frame-review.lock` 等可保留空文件；强制终止后的 stale 锁由正式入口在内核互斥下接管，active 时拒绝接管，不手工删除锁或清空账本。

只有后台明确终止且运行摘要或产物链路也显示未完成，才按本手册的“运行中断或陈旧批次”处理。策略服务未提供原始拒绝原因时，记录为 `tool_call_blocked` 与“待策略侧日志确认”；不要把业务任务、RPA 或 FFmpeg 归因为策略故障。

## 运行中断或陈旧批次

### 本版执行与恢复证据

正常信号中断在检查点保存后以 exit 130 返回，标准摘要为 interrupted，status.nextAction 为 resume；exit 5 仍表示真实的部分失败，需 inspect。SIGKILL 不能捕获，按保留的锁与检查点判断。

- `python scripts/run_standard_analysis.py version` 输出便携源码指纹；`status.runtime` / `run-summary.json.runtime` 保存实际执行代码指纹、Python/系统与 PID。旧摘要没有指纹时不得猜版本。
- `prepare` / `resume` 可传 `--download-concurrency 2 --postprocess-concurrency 2`；受限环境可降到 1。检查 `status.workerLimits` 获取实际配置；增大并发不保证更快。
- 重复标准命令在依赖加载前检查 active；真正写入前仍获取目录与业务锁。阶段切换写结构化 stderr 并立即 flush，日志不必等整轮结束才出现。
- `status` 仅读进度文件末尾至多 64 KiB，尾行截断时退回最近有效检查点。`lastProgressAgeSec` 反映已落盘进展年龄；不能据此杀进程或判 OOM。exit 137 仍需宿主资源/回收证据才能归因。
- 下载完成即原子保存 MP4 指纹，后续抽帧可复用；帧缓存绑定视频 SHA、所选时刻及 JPEG SHA，已完成画面不因其他帧中断而重抽。旧产物只有通过校验才复用。
- 队列只保留下载并发 + FFmpeg 并发数量的在途工作；真正开工才记尝试，旧 running 封口为 interrupted（已验证成功产物则恢复成功）。真实失败重试与中断恢复分别计数。
- 远端收据有 ID 时，即使 manifest 仍为 pending 也续查原 ID；仅有提交痕迹且 ID 未落盘时保留未知提交证据并隔离，禁止重新提交同素材。
- RPA 子进程本身会写 CSV/records/receipt，因此继承实际执行锁直到退出；父进程死亡或子进程自身截止时 watchdog 独立退出，亦覆盖结果队列无人读取的收尾阶段。只要旧 writer 仍活着，锁保持 active；不能因锁内原父 PID 已消失就手动删锁接管。
- 升级前确认旧进程已终止，再以新版本恢复原目录；不要让不遵守 flock 的旧运行器和新运行器混跑。锁适用于同机 POSIX flock 可靠的文件系统，不构成跨主机协调。


先读取运行目录的 `run-summary.json` 和 `peak-frames/batch-manifest.json`。若 manifest 中仍有 `running` 或 `submitted` 状态，不能直接覆盖目录。先用 `status` 确认原进程结束，再恢复同一批次。已有远程任务只轮询原 ID，不重提同一素材；补齐缺口可以提交尚未提交的候选，因此 `resume` 仍属于 RPA 操作。

`status` 核对运行目录 `.workflow-run.lock`、业务请求锁与 `.batch-run.lock`。新锁以 POSIX flock 为准，SIGKILL 后内核自动释放；旧锁兼容 pid、hostname 与 processStart，并排除僵尸进程。正常退出保留空文件，状态为 missing，不手工删除锁文件。持久摘要仍为 `running`、但所有锁都不是 active 时，对外返回 `workflowStatus=interrupted`、`persistedWorkflowStatus=running` 和 `recoveryRequired=true`；`status` 本身保持只读。不得使用 `pgrep -af` 或其他会输出完整命令行与环境变量的命令查进程；需要复核时只按锁内 pid 查存活性，不输出 args/env。

需要继续核对已提交任务时，运行标准恢复入口：

```bash
python scripts/run_standard_analysis.py resume \
  --shop-id "<授权店铺 ID>" --start-date 2026-07-19 --end-date 2026-07-25 \
  --top-n 9 --advertiser-id "1770096415226951" \
  --output-dir "/path/to/run"
```

`resume` 校验请求参数与旧摘要一致，拒绝 active 批次，自动接管陈旧工作流/批次锁。先且仅做一次 `records_only` 本地对账，收据必须为 `rpaCalls=0`、`postprocessAttemptedCount=0`；然后由同一个批处理引擎查询已保存远程 ID、补齐目标数量的有效 records，最后统一执行待完成后处理。有效 summary/JPEG 复用；不重跑报表选品，不循环调用每次两条的独立 repair。

先运行 `run_standard_analysis.py status`，按五层产物分别盘点并记录数量：`rpaRequestCount`（提交数）→ `rpaAcceptedCount`（有效诊断结果数）→ `rpaSelectedCount`（含可用 videoUrl 的选中数）→ `recordsPersistedCount`/CSV 数（诊断结果已落盘数）→ `postprocessSucceededCount`（截帧与 `summary.json`、`summary.md` 均完整数）。提交数、平台/UI 的“成功”数字、素材目录数均不等于本地落盘或完整后处理数；没有本地 CSV/records 时不得声称平台已成功或失败。

调度使用分阶段屏障：先按 RPA 并发 3 个逐波收集，当前波次的 RPA 和 CSV/records 落盘全部终态后即可进入下一波；达到目标 records 数前不启动视频下载或 FFmpeg。本地后处理默认视频下载并发 2、FFmpeg 并发 2；下载与截帧使用独立信号量。后处理终态失败时，才对下一顺位候选素材执行独立的 RPA 补位阶段。

若用户未明确授权再次调用 RPA，且原进程已结束，可运行“修复保留产物”章节的 `repair_manifest.py --records-only`。它优先核验 `rpa-receipts/<materialId>.json` 的 `records_persisted` 收据，将有效 receipt/CSV 恢复为 records，并核对已有 summary/JPEG，不下载、不截帧。数量不足或目标数量未知时，即使省略 `--records-only` 也会阻止后处理；只有目标数量的有效 records 已齐备，独立 repair 才能补跑缺失后处理。检查收据的 `rpaCalls`、`postprocessAttemptedCount` 和 `postprocessBlockedReason`。

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

`--resume` 对带已保存 `task_group_id` 的 `submitted` 素材继续轮询同一远程任务。已提交但没有本地 CSV/records、也没有可复用远程任务 ID 的素材标记失败，不重提该素材；引擎可以用尚未提交的候选补齐缺口。用户要求停止 RPA 时只使用 `--records-only` 本地对账，并把悬空素材保留为待对账。

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

该命令先执行 `records_persisted receipt -> records`、`CSV -> records` 和 `summary/JPEG -> manifest` 对账，不重新调用 RPA。加入 `--records-only` 时到此结束；否则必须先证明 `targetTopN` 个不同素材有有效 records 和可用 videoUrl，才执行 `records -> 后处理`。采集不足或目标数量未知时返回退出码 5、`postprocessBlockedReason=rpa_collection_incomplete`，不要重复调用等待其自动补齐。

下载与 FFmpeg 分别受独立并发上限控制；独立 repair 默认每次只启动 2 条素材，视频下载并完成大小/SHA-256 后立即 checkpoint，整条素材完成后再次 checkpoint。仅当未被屏障阻止且本次有实际进展时，才按 `postprocessRemainingCount` 决定是否继续独立修复；标准 `resume` 直接使用共享批处理队列，无此循环。不要再用外部 `timeout` 在任意时刻杀进程。若 manifest 缺失，再传入 `--top-materials-json`；无法证明诊断阶段时保持 `rpaStatus: unknown`。`postprocessAttemptCount` 是公平调度计数，`postprocessAttempts` 始终是尝试记录列表；旧错误版本留下的整数会自动迁移到计数字段。

## 报告生成中断与续跑

`finalize` 按 `reportData → workbook(可省略) → html → archive(可省略)` 写入 `final-report/report-build-receipt.json`。标准入口默认生成可直接打开的 `final-report/` 目录和高光帧 XLSX，ZIP 仍需 `--archive`；明确省略 Excel 才用 `--skip-workbook`，`--include-workbook` 保留兼容。收据绑定候选分析、质量复核、冻结输入索引、records/summary 和渲染代码哈希；输入未变化且阶段产物存在时，重跑只续接未完成阶段。摘要处于 `running/report` 且旧进程已结束，或 `status=complete` 需补 Excel/归档时，直接重跑同一 `finalize`，不要手工改回 `analysis_pending`。仍执行事实、帧审阅、候选与内容复核门禁；只改变 Excel 选项不重建 reportData，但会刷新 HTML 和使旧归档收据失效。源码升级会使旧版本收据失效，首次升级补建可能重建报告投影。

高光帧 XLSX 使用最长边 112px、JPEG quality 72 的临时缩略图。完整 JPEG 始终复制到 HTML 报告资产，不降低网页证据质量。报告视频直接复用已下载 MP4，优先硬链接、失败才拷贝，不再为报告单独转码。若任一阶段被 SIGKILL，确认旧进程结束后直接重跑同一 `finalize`；已写好的 report data、presentation 和视频资产会复用。

检查保留的 `diagnosis-records/`、`diagnosis-csv/`、素材 `summary.json` 与 JPEG，而不是仅凭文件名判断成功。RPA 输出字段、下载上限和波次状态的完整约束见 [RPA 输出契约](rpa-output-contract.md)。


## 交付后源视频清理

finalize 在报告校验通过、摘要为 complete 后默认保留 `peak-frames/video-assets/<materialId>.mp4`，避免把大文件清理校验并入交付关键路径。需要回收空间时，必须先通过 `finalize --archive` 显式生成并校验 ZIP，然后再独立执行下方 `cleanup-videos --mode dry-run/delete`；默认无归档路径不允许删除源视频。

清理前核验每个源视频及报告引用路径的大小、SHA，HTML 引用本地资产、ZIP 的必需条目完整且归档内容与磁盘字节一致；禁止符号链接和目录级删除。任一前置校验失败不删除，单文件删除失败记 partial。未列入 manifest 的文件保留。

```bash
python scripts/run_standard_analysis.py cleanup-videos --output-dir "$PWD/千川数据资源/<run-id>" --mode dry-run
python scripts/run_standard_analysis.py cleanup-videos --output-dir "$PWD/千川数据资源/<run-id>" --mode delete
```

`final-report/video-resource-cleanup.json` 在删除前保存 planned 清单，逐个反读并记账，支持失败重试和中断恢复。清单绑定已验证 ZIP 哈希，因此是 ZIP 外的独立审计附件，不将它回打包进该 ZIP。`run-summary.json.videoResourceCleanup` 单列 status、releasedBytes 和 rerenderRequiresSourceRecovery；清理失败不回退 complete 报告到 analysis_pending，不得重跑分析或渲染来重试清理。重复清理不重复累计释放字节。已清理运行若需重新抽帧/渲染，应先恢复源视频；不能用预览冒充原始输入。

## Chapter 04 分类过粗或条目过少

先比对候选 AI JSON 与 presentation-report.json：若数量相同，修正模型分析，不改前端截取条数。新 Prompt（mechanism-depth-v1）要求本批次默认 3–5 个主导画面机制类别；标准分类/本批次分类每个非空组默认一条共性结论和一个裂变方向，结构接受 1–3 条。54 帧归入一个混合总类仍须审查真实机制边界；每部分各一条无需虚构证据不足。

分类不足填写 classificationLimitation，组内结论/方向不足填写 contentLimitation；说明不足后仍需 evidence_limited 复核，不能以“缺少因果证明”为由忽略可见画面差异。数量门槛通过后继续检查语义边界和可测试变量，不用脚本拆类或复制条目凑数。新规则由冻结 Prompt 启用；重做历史输入时创建新候选并重新绑定索引和复核哈希，不覆盖历史成品。

耗时解释：sleep 可与后台业务重叠，超时前也可能已落盘有效成果，二者不能全算浪费；工具 duration 不含完整后台生命周期。分别记录首次启动、RPA 收齐、后处理完成、analysis_pending、候选首次/最终校验、complete 和复盘起点；缺少起止证据标 unknown。完整 Bash 汇总不证明模型已读完所有事实页或已看完所有图片。
