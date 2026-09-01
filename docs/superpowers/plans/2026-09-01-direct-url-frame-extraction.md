# 方案一：FFmpeg 直接读取 URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保留现有本地输入兼容性的前提下，让 FFmpeg 可直接读取 `material_info.videoUrl` 并完成精确峰值截帧。

**Architecture:** 增加 URL 输入适配层和 HTTP 能力探测层；URL 模式沿用现有记录解析、峰值选择、JPEG 原子发布和 summary 生成，但避免把完整 MP4 落盘。对不支持 seek/Range 或超过内存预算的输入，明确走安全降级路径。

**Tech Stack:** Python 3.13、requests、FFmpeg/imageio-ffmpeg、pytest。

**Spec:** `docs/superpowers/specs/2026-09-01-direct-url-frame-extraction-design.md`

## Global Constraints

- 默认本地文件模式行为不变。
- URL 模式不得把完整视频读入 Python 内存。
- 不记录完整签名 URL，不保留 MP4 产物。
- 使用 `runs/20260901-rpa6` 做真实 URL 验证，不调用 RPA。

### Task 1: 建立 URL 能力探测测试

**Files:**
- Create: `tests/test_url_video_input.py`
- Modify: `scripts/rpa_adapter.py`

- [ ] 写测试覆盖 HEAD 不可用时 GET 探测、`Accept-Ranges`、`Content-Length`、`moov` 位置解析和签名 URL 脱敏日志。
- [ ] 运行 `pytest tests/test_url_video_input.py -q`，确认新增接口尚未实现而失败。
- [ ] 实现最小探测接口，返回结构化 `UrlCapabilities`，不落盘完整视频。
- [ ] 重新运行测试，确认全部通过。
- [ ] 提交 `test: cover signed URL capability probing`。

### Task 2: 扩展 FFmpeg 输入抽象

**Files:**
- Modify: `scripts/peak_frame_lib.py`
- Test: `tests/test_peak_frames.py`

- [ ] 先添加 URL 输入成功、URL 错误映射、local 行为不变的失败测试。
- [ ] 运行对应 pytest，确认失败。
- [ ] 将视频输入抽象为本地路径或 URL，避免 URL 模式强制 `Path.is_file()`。
- [ ] 让 probe 和 extraction 在 URL 模式避免无意的重复传输，并保留时长校验。
- [ ] 运行 `pytest tests/test_peak_frames.py -q`。
- [ ] 提交 `feat: support direct URL video input`。

### Task 3: 为 URL 模式增加安全截帧策略

**Files:**
- Modify: `scripts/peak_frame_lib.py`
- Test: `tests/test_peak_frames.py`

- [ ] 添加高分辨率、多目标时间点触发安全路径的测试。
- [ ] 运行测试确认失败。
- [ ] 实现 URL 模式内存预算和不支持 seek/Range 时的明确降级；禁止无界 `split=6`。
- [ ] 保持 JPEG staging 原子发布及 missing-frame 语义。
- [ ] 运行相关单测和 `pytest -q`。
- [ ] 提交 `feat: bound memory for URL frame extraction`。

### Task 4: CLI、真实数据验证与回归

**Files:**
- Modify: `scripts/extract_peak_frames.py`
- Create: `scripts/verify_url_video_input.py`
- Test: `tests/test_peak_frames.py`, `tests/test_url_video_input.py`

- [ ] 添加 `--video-input-mode url`，默认值保持 `local`。
- [ ] 用 `runs/20260901-rpa6/diagnosis-records` 中的真实 URL 运行能力探测，不触发 RPA。
- [ ] 用 1 个真实素材完成 URL 模式 6 帧截取，核对 summary 和 JPEG。
- [ ] 运行完整 `pytest -q`，记录耗时、下载字节、Range、moov 和 RSS。
- [ ] 提交 `test: validate direct URL extraction on retained RPA data`。
