# RPA 视频 URL 兜底（migrate-direct-material-data）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在原始临时 MP4 后处理链路中加入 API URL 兜底，并解耦 records 持久化与 URL 可用性。

**Architecture:** 候选素材导出阶段从 API 原始字段生成 URL 映射；RPA CSV 解析阶段无论 URL 是否为空都先生成标准化 records；后处理阶段按 RPA URL→API URL 顺序解析并校验，再沿用现有下载和 FFmpeg 路径。

**Tech Stack:** Python、JSON/CSV、requests、FFmpeg、pytest。

**Spec:** `docs/superpowers/specs/2026-09-02-rpa-url-fallback-migrate-design.md`

## Global Constraints

- 不修改 RPA 请求、账号、波次、并发、超时和重试协议。
- 必须按 materialId 关联 URL，不得按素材名称关联。
- 不把签名查询参数写入日志、异常或 summary。
- CSV 完全缺失时不得伪造诊断 records。

### Task 1: API URL 进入候选素材契约

**Files:** Modify `scripts/top_materials_lib.py`, `scripts/export_top_materials.py`; Test `tests/test_top_materials.py`.

- [ ] 写失败测试：构造 `roi2_material_video_play_info`，断言输出条目包含 `videoUrl` 与 `videoUrlCandidates`。
- [ ] 运行 `pytest tests/test_top_materials.py -q`，确认新增断言失败。
- [ ] 实现嵌套 Value/ValueStr 解析、URL 候选过滤和 materialId 映射。
- [ ] 运行同一测试，确认通过并检查 `git diff --check`。
- [ ] 提交：`feat: preserve API video URLs in top materials`。

### Task 2: records 持久化与 URL 预检解耦

**Files:** Modify `scripts/extract_peak_frames.py`, `scripts/rpa_runtime.py`; Test `tests/test_peak_frames.py`, `tests/test_rpa_artifact_receipts.py`.

- [ ] 写失败测试：CSV 有效但 videoUrl 为空时仍生成 diagnosis-records，并保持失败原因可追踪。
- [ ] 运行目标测试确认 RED。
- [ ] 将 CSV 结构/素材身份校验与 URL 可用性校验拆开；先写 records，再进入 URL 解析。
- [ ] 运行目标测试确认 GREEN，验证收据 recordsPath 已回写。
- [ ] 提交：`fix: persist RPA records before video URL validation`。

### Task 3: 原始后处理 URL 解析与兜底

**Files:** Modify `scripts/extract_peak_frames.py`; Test `tests/test_peak_frames.py`.

- [ ] 写 RPA 优先、API 兜底、双空失败、备用 URL 失效测试。
- [ ] 运行测试确认 RED。
- [ ] 实现统一 resolver，返回 URL、来源和失败原因；接入现有临时下载路径。
- [ ] 运行相关测试及 `pytest tests/test_peak_frames.py -q`。
- [ ] 提交：`feat: fall back to API video URL in local postprocess`。

### Task 4: 回归与产物契约核验

- [ ] 运行 `pytest tests/test_top_materials.py tests/test_peak_frames.py tests/test_rpa_artifact_receipts.py -q`。
- [ ] 检查 manifest 的来源字段、recordsPath、无 URL 失败分类和无 MP4 残留。
- [ ] 运行 `git diff --check`，确认只涉及本计划文件和目标模块。
- [ ] 提交：`test: cover API video URL fallback contract`。
