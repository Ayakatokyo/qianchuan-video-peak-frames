# RPA 视频 URL 兜底（direct-url-frame-extraction）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 direct-url 后处理在 RPA URL 为空时使用 API URL，并保留有效 RPA records。

**Architecture:** 复用候选素材导出的 API URL 字段和统一 URL resolver；records 持久化先于 URL 校验；direct-url probe、FFmpeg 输入和 manifest 仅消费 resolver 结果。

**Tech Stack:** Python、requests、FFmpeg、pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-rpa-url-fallback-direct-url-design.md`

## Global Constraints

- 不修改 RPA 请求和调度。
- materialId 精确关联；不使用 thumbnailUrl。
- 不记录签名 URL 查询参数。
- 保持默认 local 模式兼容。

### Task 1: 同步 API URL 候选字段

**Files:** Modify `scripts/top_materials_lib.py`, `scripts/export_top_materials.py`; Test `tests/test_top_materials.py`.

- [ ] 先写嵌套 API URL 解析失败测试并确认 RED。
- [ ] 实现 `videoUrl`/`videoUrlCandidates` 输出，运行目标测试确认 GREEN。
- [ ] 提交：`feat: expose API video URL candidates`。

### Task 2: 解耦 records 落盘门槛

**Files:** Modify `scripts/extract_peak_frames.py`; Test `tests/test_peak_frames.py`.

- [ ] 写 CSV 有效、URL 为空仍写 records 的失败测试。
- [ ] 运行 RED；拆分 records 校验与 video URL 校验。
- [ ] 运行 GREEN，确认 receipt 和 manifest 指向 records 文件。
- [ ] 提交：`fix: retain records when RPA video URL is empty`。

### Task 3: 接入 direct-url resolver

**Files:** Modify `scripts/rpa_adapter.py`, `scripts/extract_peak_frames.py`; Test `tests/test_url_video_input.py`, `tests/test_peak_frames.py`.

- [ ] 写 RPA 优先、API fallback、候选 URL 重试和脱敏测试。
- [ ] 运行 RED；实现 resolver 与 direct-url probe 接入。
- [ ] 运行 URL/峰值帧回归，确认 fallback 只改变输入 URL 来源。
- [ ] 提交：`feat: use API URL fallback for direct frame extraction`。

### Task 4: 集成验证

- [ ] 使用保留 CSV/API fixture 做无 RPA 集成测试，确认 6 帧和 summary 契约。
- [ ] 运行 `pytest tests/test_url_video_input.py tests/test_peak_frames.py -q`。
- [ ] 核对无 URL、URL 失效、签名脱敏和无 MP4 残留。
- [ ] 提交：`test: verify direct URL fallback integration`。
