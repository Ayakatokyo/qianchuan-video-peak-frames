# RPA 与后处理分阶段调度测试设计

## test_type_decision_card

- 变更类型：本地 CLI 的异步状态机与资源并发边界，不涉及 HTTP、SSE 或前端。
- 必需验证：调度器单元回归、真实 CLI 入口的无网络等价测试、manifest 契约回归、中断/恢复路径回归。
- 不适用：线上服务、Token、Browser、Agent SSE、真实 RPA。测试不得发起外部请求。

## risk_ledger

| 风险 | 期望 | 验证落点 |
|---|---|---|
| RPA 与视频后处理仍然重叠 | 所有目标 RPA records 落盘后才能进入后处理 | `test_rpa_collection_finishes_before_postprocess_starts` |
| 下载并发失控 | 下载同时最多 2 个 | `test_wave_postprocess_enforces_download_and_ffmpeg_limits` |
| FFmpeg 争抢 CPU/磁盘 | 截帧同时最多 1 个 | 同上 |
| FFmpeg 失败导致重复下载 | 已验证的本地 MP4 在截帧重试时复用 | `test_wave_postprocess_reuses_download_after_frame_failure` |
| 后处理失败后无法补位 | 只对下一顺位素材补发 RPA | 现有 backfill 回归 |
| 恢复时重复提交 RPA | 只复用 records/既有任务信息 | 现有 resume/receipt 回归 |

## boundary_coverage

- 成功路径：RPA 全部成功 → 下载并发 2 → FFmpeg 并发 1 → ready 文件。
- RPA 失败：当前 wave 终态后只补齐 records 缺口，不等待任何后处理。
- 后处理失败：本地重试不重新提交 RPA；截帧重试不重新下载已验证 MP4。
- 恢复路径：已存在 summary 直接复用；仅 records 存在时进入本地后处理。
- 参数边界：默认 `rpa=3, download=2, ffmpeg/postprocess=1`；显式参数和旧 `--concurrency` 兼容。

## red_blue_protocol

- 攻击假设 A：让首个后处理任务阻塞，观察是否还有未完成的 RPA。裁判：后处理首次调用时 RPA 收集已达目标。
- 攻击假设 B：同时提交多个大视频。裁判：下载活跃数不超过 2，FFmpeg 活跃数不超过 1。
- 攻击假设 C：下载成功后人工使 FFmpeg 首次失败。裁判：第二次只执行截帧，下载调用总数仍为 1。

## runtime_path_matrix

| 入口 | 控制面 | 运行面 | 证据 |
|---|---|---|---|
| `extract_peak_frames.py --source rpa` | batch runner | 本地线程池 + RPA adapter | unittest + manifest/ready 文件 |
| `repair_manifest.py` | repair coordinator | 本地视频下载/FFmpeg | unittest + repair manifest |

## state_machine_oracle

`pending → rpa submitted → records_persisted → postprocess queued → downloading/frame_extraction → succeeded|failed`。任何 `downloading` 或 `frame_extraction` 发生前，`rpaCollection.acceptedCount` 必须已达到当前可收集目标；后处理终态失败后才进入下一候选素材的 RPA 补位。

## scenario_materialization

- 调度顺序、补位、中断：`tests/test_rpa_batch_runner.py`
- 参数、manifest、下载/FFmpeg 资源上限、视频复用：`tests/test_peak_frames.py`
- 恢复路径：`tests/test_repair_manifest.py`
- 持久化契约：`tests/test_schemas.py`

## test_effectiveness_ledger

- 2026-09-20：本变更相关的 262 项单元/契约测试通过，覆盖调度顺序、下载/FFmpeg 独立并发、视频复用、恢复 checkpoint、schema 和技能文档契约。
- 全仓 616 项中 3 项失败、1 项跳过；失败均位于本变更未修改的 Chapter 01 提示词旧断言与报告视频转码基线，不归因于本调度改造。
- 本轮只使用离线 fixture 与 mock，不发起真实 RPA、视频网络下载或真实 FFmpeg 作业。

## regression_candidate_plan

本仓库没有独立的长期回归 manifest；上述四组用例直接进入常规 unittest 套件，作为长期回归候选。
