# RPA 视频 URL 兜底（http-pipe-frame-extraction）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 HTTP pipe 后处理增加 API URL 兜底，同时保证 records 和 pipe 生命周期契约不变。

**Architecture:** API 候选 URL 在 top-materials 阶段标准化；RPA CSV 先解析并持久化 records；统一 resolver 选择 RPA/API URL；现有 pipe 传输、Range 重排和顺序 FFmpeg 逻辑保持不变。

**Tech Stack:** Python、requests、FFmpeg stdin pipe、pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-rpa-url-fallback-http-pipe-design.md`

## Global Constraints

- 不修改 RPA 请求、波次、并发和重试。
- 不落日志签名参数；不使用 thumbnailUrl。
- 不凭 API URL 伪造缺失的诊断 records。
- 保持 local/direct/pipe 输入边界清晰。

### Task 1: 保存 API URL 候选

**Files:** Modify `scripts/top_materials_lib.py`, `scripts/export_top_materials.py`; Test `tests/test_top_materials.py`.

- [ ] 写 API `PlayResource.UrlList` 解析失败测试并确认 RED。
- [ ] 实现字段输出和 materialId 映射，运行 GREEN。
- [ ] 提交：`feat: retain API video URLs for pipe fallback`。

### Task 2: records 先行持久化

**Files:** Modify `scripts/extract_peak_frames.py`, `scripts/rpa_adapter.py`; Test `tests/test_peak_frames.py`, `tests/test_http_pipe.py`.

- [ ] 写 CSV URL 为空仍生成 records 的 RED 测试。
- [ ] 拆分 records 有效性与 URL 可用性，运行目标测试 GREEN。
- [ ] 确认 receipts 的 recordsPath 与 manifest 状态一致。
- [ ] 提交：`fix: persist diagnosis records before pipe URL resolution`。

### Task 3: pipe 接入 URL fallback

**Files:** Modify `scripts/rpa_adapter.py`, `scripts/extract_peak_frames.py`; Test `tests/test_http_pipe.py`.

- [ ] 写 resolver 来源、候选重试、失效分类和脱敏测试。
- [ ] 运行 RED；接入 API fallback，保持 `stream_video_to_ffmpeg` 接口不变。
- [ ] 运行 `pytest tests/test_http_pipe.py -q`，确认背压、断流、超时、moov 重排回归。
- [ ] 提交：`feat: stream API fallback URL into FFmpeg pipe`。

### Task 4: 保留数据集成验证

- [ ] 使用无 RPA 的保留 CSV/API fixture 验证 pipe 输出帧、summary 和来源字段。
- [ ] 运行 `pytest tests/test_http_pipe.py tests/test_peak_frames.py -q`。
- [ ] 核对无 MP4/staging 残留、签名脱敏和 CSV 缺失时的停止行为。
- [ ] 提交：`test: cover HTTP pipe API URL fallback`。
