# 方案一：FFmpeg 直接读取签名 URL 设计

## 目标

让 FFmpeg 直接消费 `material_info.videoUrl`，避免 Python 先把完整 MP4 下载到本地再交给 FFmpeg，同时保留当前峰值选择、精确时间戳、JPEG 输出和失败语义。

## 现状与约束

- `top_materials.json` 只提供素材候选和 `materialId`；URL 来自诊断记录的 `material_info.videoUrl`。
- 当前 `process_records()` 要求 `video_path` 是本地文件，并先 probe 再执行批量截帧。
- 签名 URL 可能过期；HTTP Range、连接重试和 MP4 `moov` 位置不能假设，必须用 `runs/20260901-rpa6` 的真实 URL 验证。
- 当前 `split=6 + trim` 批处理存在高内存峰值；本方案不得把网络输入接到未经保护的批处理上。
- 输出仍只保留 JPEG、`summary.json`、`summary.md`，不保留视频。

## 数据流

1. 从真实诊断记录解析 `material_info.videoUrl`。
2. 对 URL 做 HTTP 能力探测：状态码、`Content-Length`、`Accept-Ranges`、响应头和 MP4 `moov` 位置。
3. 将 URL 作为 FFmpeg 输入源，单次或最少次数启动 FFmpeg。
4. FFmpeg 输出目标时间点 JPEG；输出通过临时 staging 文件原子发布。
5. 写入现有 summary 结构，清理 staging 文件，不产生 MP4 缓存。

## 接口设计

- `process_records(..., video_source: str | Path, source_kind: Literal["local", "url"])`
- `probe_video_duration()` 增加 URL 输入能力；避免在 URL 模式下无必要地重复传输，优先使用已验证的 `material_info.videoDuration`，并在 FFmpeg 解码失败时回退为显式 probe。
- `extract_frames_batch()` 接受 URL，但必须使用受控内存策略；当 URL 不支持 seek/Range 或 batch 内存估算超限时，返回明确的降级原因。
- CLI 增加显式开关（例如 `--video-input-mode url`），默认保持现有本地文件行为。

## HTTP/FFmpeg 行为

- 先对一个真实 URL 做 `HEAD`/小范围 `GET` 验证；若 HEAD 不可靠，以 GET 响应为准。
- 若服务器支持 Range 且 `moov` 位于文件头部，允许 FFmpeg 从 URL 读取到最大目标时间附近；否则仍允许顺序读取，但不得声称减少网络字节数。
- FFmpeg 网络错误必须映射到现有 `FfmpegError`，保留素材级失败，不影响其他素材。
- 不把签名 URL 写入新的日志或报告；日志只记录 URL 主机、状态、Range 能力和耗时。

## 内存与并发

- URL 输入模式默认后处理并发为 1，验证稳定后再开放 2。
- 禁止在高分辨率/长视频/多目标时间点上直接使用未经估算的 `split=6 + trim`。
- 记录 FFmpeg 子进程 RSS、下载字节数和网络等待时间，区分网络瓶颈与解码瓶颈。

## 验收标准

- 使用 `runs/20260901-rpa6` 中至少 1 个真实签名 URL 成功完成 6 帧输出。
- URL 模式和现有本地模式的帧文件名、目标时间戳、summary 字段一致。
- 明确记录 URL 是否可被 FFmpeg 直接读取、是否支持 Range、`moov` 是否在文件头。
- 不产生持久 MP4；失败时临时文件和进程均被清理。
- 现有测试全部通过，并新增 URL 输入、HTTP 探测、失败降级和生命周期测试。
