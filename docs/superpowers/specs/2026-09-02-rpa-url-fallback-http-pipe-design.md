# RPA 视频 URL 兜底设计（http-pipe-frame-extraction）

## 背景

本分支通过 HTTP 流式传输把视频写入 FFmpeg stdin，避免完整 MP4 落盘。其输入仍来自 RPA `material_info.videoUrl`，因此本次 RPA CSV URL 为空时，pipe 流程同样无法启动。API 原始数据中的 `PlayResource.UrlList` 可作为备用输入。

## 目标行为

将 API URL 保存到 `top_materials.json`，在有效 RPA CSV 已落盘但 RPA URL 为空时按 materialId 回退到 API URL，并继续执行 HTTP pipe。先持久化 diagnosis records，再决定视频 URL；CSV 缺失时不伪造 records。

## 管道约束

- resolver 输出 URL、来源和候选列表；pipe 传输层不关心 URL 来源。
- 保留 Range/moov 重排、背压、总截止时间和异步清理逻辑。
- API URL 失效时尝试候选地址；所有错误脱敏。
- 不因 fallback 改变 RPA 波次和并发行为。

## 验收条件

1. API URL 候选正确写入 `top_materials.json`。
2. RPA URL 非空时 pipe 使用 RPA URL。
3. RPA URL 为空时 pipe 使用 API URL 并完成帧输出。
4. pipe 的 HTTP、FFmpeg 生命周期测试不回归。
5. 有效 CSV 生成 diagnosis-records；CSV 缺失时明确停止。
