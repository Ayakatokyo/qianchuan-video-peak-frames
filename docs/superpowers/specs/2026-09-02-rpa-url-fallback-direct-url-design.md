# RPA 视频 URL 兜底设计（direct-url-frame-extraction）

## 背景

本分支的后处理由本地 MP4 改为 FFmpeg 直读 URL，并已有 URL 能力探测和降级逻辑。当前 RPA CSV 的 `material_info.videoUrl` 为空，使直读逻辑在进入前就被跳过。API 原始报表数据包含 `PlayResource.UrlList`，可作为同一素材的备用视频输入。

## 目标行为

将 API URL 写入 `top_materials.json`。后处理优先使用 RPA CSV 的 URL；RPA URL 为空时，按 `materialId` 使用 API URL 进行能力探测并让 FFmpeg 直读。有效 CSV 必须先生成 `diagnosis-records`，不能因 URL 为空丢失诊断数据。

## 设计边界

- 只改落盘后的 URL 解析和 direct-url 输入，不改 RPA 请求流程。
- API URL 字段来自 `roi2_material_video_play_info.Value/ValueStr.PlayResource.UrlList`。
- 按候选顺序探测 URL；失败时尝试下一候选。
- 签名参数只在运行时传给 FFmpeg，不进入日志、manifest 或报告。
- CSV 完全缺失时不生成 records；API URL 不替代 RPA 诊断数据。

## 验收条件

1. `top_materials.json` 包含 API URL 候选。
2. RPA URL 有效时 direct-url 行为和来源为 `rpa`。
3. RPA URL 为空时 API URL 能通过 probe 并完成直读截帧。
4. URL 失效时保留明确失败原因，不泄露签名。
5. 有效 CSV 始终生成 `diagnosis-records`，且 direct-url 与旧 local 帧结果一致。
