# 方案二：HTTP 流式管道截帧 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 HTTP 流式读取和 FFmpeg stdin 管道完成峰值截帧，去掉完整 MP4 临时文件并重叠传输与解码。

**Architecture:** Python 负责 HTTP 生命周期、大小/截止时间/重试和背压；FFmpeg 作为单次顺序解码器从 `pipe:0` 读取；帧提取和 summary 沿用现有结构，输入模式通过显式 CLI 开关隔离。

**Tech Stack:** Python 3.13、requests、subprocess、FFmpeg/imageio-ffmpeg、pytest。

**Spec:** `docs/superpowers/specs/2026-09-01-http-pipe-frame-extraction-design.md`

## Global Constraints

- 默认 local 模式不变。
- 禁止把完整视频或完整响应缓存到 Python 内存。
- URL 只用于真实 `runs/20260901-rpa6` 数据验证，不调用 RPA。
- 管道失败必须清理 FFmpeg、响应和 JPEG staging 文件。

### Task 1: 编写管道传输失败测试

**Files:**
- Create: `tests/test_http_pipe.py`
- Modify: `scripts/rpa_adapter.py`

- [ ] 添加测试覆盖固定 chunk、大小上限、总截止时间、HTTP 错误、stdin 写入异常和响应关闭。
- [ ] 运行 `pytest tests/test_http_pipe.py -q`，确认失败。
- [ ] 实现 `stream_video_to_ffmpeg()`，使用阻塞写入实现背压并返回统计数据。
- [ ] 重新运行测试，确认通过。
- [ ] 提交 `test: cover HTTP to FFmpeg pipe lifecycle`。

### Task 2: 实现单次管道截帧器

**Files:**
- Modify: `scripts/peak_frame_lib.py`
- Test: `tests/test_peak_frames.py`, `tests/test_http_pipe.py`

- [ ] 先添加 pipe 模式输出与 local 模式一致的失败测试。
- [ ] 运行测试确认失败。
- [ ] 实现 `extract_frames_from_stream()`，从 `pipe:0` 顺序解码并输出 staging JPEG。
- [ ] 使用诊断记录时长校验目标范围，避免单独 probe 造成重复 HTTP 读取。
- [ ] 禁止无界 `split=6 + trim`，确保单次管道解码有界内存。
- [ ] 运行相关 pytest。
- [ ] 提交 `feat: extract peak frames from HTTP pipe`。

### Task 3: 接入 CLI 和后处理调度

**Files:**
- Modify: `scripts/extract_peak_frames.py`
- Modify: `scripts/rpa_batch_runner.py`
- Test: `tests/test_peak_frames.py`

- [ ] 添加 `--video-input-mode pipe`，仅在 URL 可用且未显式提供 local video 时启用。
- [ ] 保持每波 RPA 落盘后立即提交后处理的调度语义。
- [ ] 将 pipe 统计写入现有 `timingsSec`，增加 `videoStreamBytes` 和 `videoStreamSec`。
- [ ] 运行调度、失败恢复和 summary 回归测试。
- [ ] 提交 `feat: wire pipe input into RPA postprocess`。

### Task 4: 使用真实已落盘数据验证

**Files:**
- Create: `scripts/benchmark_http_pipe.py`
- Test: `tests/test_http_pipe.py`

- [ ] 使用 `runs/20260901-rpa6` 中的 1 个真实素材执行 pipe 模式，确认 6 帧和 summary 落盘。
- [ ] 对比同一 URL 的 local 下载模式：总耗时、传输字节、RSS、帧时间戳和 JPEG 哈希。
- [ ] 模拟一次断流/超时，确认素材级失败且无残留 MP4/staging 文件。
- [ ] 运行完整 `pytest -q` 并保存验证结果。
- [ ] 提交 `test: benchmark HTTP pipe against retained RPA data`。
