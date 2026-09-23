# 方案二：HTTP 流式管道截帧设计

## 目标

让 Python 以流式方式读取 `material_info.videoUrl`，通过受控 stdin 管道送入单个 FFmpeg 进程，避免完整 MP4 落盘，并使网络传输与解码重叠。

## 现状与约束

- 真实流程是每波 3 个 RPA 结果落盘后立即进入后处理。
- `download_video()` 已具备 1 MiB chunk、大小上限、总截止时间、重试和 staging 清理能力。
- 管道不可 seek，必须从视频开头顺序解码到最大目标时间。
- 不得依赖第二次独立 probe 导致重复传输；优先使用诊断记录中的 `videoDuration` 做边界校验。
- 当前 `split=6 + trim` 的内存风险必须在管道方案中消除或受控。

## 数据流

1. 解析 URL 与点击曲线，计算目标时间点和最大目标时间。
2. 启动 FFmpeg，输入为 `pipe:0`，输出为 staging JPEG。
3. Python 以 1 MiB chunk 读取 HTTP 响应并写入 FFmpeg stdin；写入阻塞形成背压。
4. FFmpeg 在单次顺序解码中产生目标帧；Python 等待进程结束并校验 JPEG。
5. 写入 summary，关闭响应和 stdin；不保留 MP4。

## 接口设计

- `stream_video_to_ffmpeg(video_url, ffmpeg_process, ..., max_bytes, deadline_sec)`：只负责 HTTP 流和进程 stdin，返回字节数及网络耗时。
- `extract_frames_from_stream(records, video_url, output_dir, ...)`：负责一次性管道截帧和 summary 所需的帧结果。
- CLI 增加显式开关（例如 `--video-input-mode pipe`），默认仍为 `local`。

## 错误处理

- HTTP 状态错误、读取超时、总截止时间、大小超限均终止 FFmpeg 并清理 staging 文件。
- FFmpeg 非零退出时保留素材级失败原因，不影响同波其他素材。
- 管道中断后的重试重新建立 HTTP 请求和 FFmpeg 进程；不实现断点续传。
- 只记录主机、状态、字节数和耗时，不记录完整签名 URL。

## 内存与精度

- 管道缓冲由 OS pipe 和固定 chunk 控制，不把视频累计到 Python 内存。
- 必须使用单次顺序解码的有界内存截帧实现；禁止直接复用无界 `split=6 + trim`。
- 帧时间戳仍以原始 `CLICK_COUNT` 秒为准；测试需比较 pipe 与 local 模式的文件名和 summary 时间戳。

## 验收标准

- 使用 `runs/20260901-rpa6` 的真实 URL 完成至少 1 个素材的 6 帧输出。
- 记录总耗时是否从“下载 + 截帧”变为接近两者最大值。
- 记录 Python RSS、FFmpeg RSS、传输字节数和失败重试行为。
- 不产生持久 MP4，HTTP 响应、stdin、FFmpeg 进程均关闭。
- 现有测试全部通过，并新增管道背压、超时、断流、JPEG 校验和生命周期测试。
