# RPA 视频 URL 兜底设计（migrate-direct-material-data）

## 背景

本分支保留“RPA CSV 落盘后下载临时 MP4，再由 FFmpeg 截帧”的原始后处理方式。实测中 RPA CSV 的 `material_info.videoUrl` 为空，导致素材在可用性预检阶段被拒绝，标准化 `diagnosis-records/*.json` 也未生成，后处理无法启动。千川报表 API 原始结构中的 `roi2_material_video_play_info.PlayResource.UrlList` 已包含对应视频地址。

## 目标行为

当 RPA CSV 已落盘但 `videoUrl` 为空时，仍持久化诊断 records，并按 `materialId` 使用 `top_materials.json` 中的 API URL 作为兜底；RPA URL 非空时始终优先使用 RPA URL。RPA 请求、波次、并发、重试和 CSV 获取协议不变。

## 数据契约

- `top_materials.json.materials[*].videoUrl`：API 原始报表数据解析出的首选视频 URL。
- `top_materials.json.materials[*].videoUrlCandidates`：`UrlList` 中的候选 URL，按原顺序保存。
- `diagnosis-records/<materialId>.json`：只要 CSV 能解析且 `materialId` 匹配就生成，不再以 URL 非空作为生成门槛。
- manifest 记录 `videoUrlSource`（`rpa` / `api_fallback`）和 `videoUrlFallbackReason`。

## 处理流程

```text
API 原始数据 → top_materials.json URL 映射
RPA CSV → 解析并持久化 diagnosis-records
                       ↓
             RPA videoUrl 优先
                       ↓为空
             API videoUrl 兜底
                       ↓
              临时 MP4 下载 → FFmpeg 截帧
```

API URL 必须按 `materialId` 关联，经过 HTTP 状态、内容类型和签名有效性校验；不使用 `thumbnailUrl`。两类 URL 均不可写入日志或错误文本。

## 失败与兼容边界

- CSV 不存在、损坏或素材 ID 不匹配：`rpa_csv_artifact_unavailable` / `rpa_payload_invalid`，不得仅凭 API URL 构造完整诊断 records。
- CSV 存在但 RPA/API URL 均不可用：`missing_video_url`。
- API URL 全部失效：`api_video_url_invalid`，保留 records 供复盘。
- RPA URL 正常时，下载和输出契约保持原行为。

## 验收条件

1. API URL 被写入候选素材条目，并可按 materialId 查找。
2. RPA URL 非空时来源标记为 `rpa`，API URL 不被使用。
3. RPA URL 为空时，使用 API URL 完成临时下载和原有 FFmpeg 截帧。
4. CSV 有效但 URL 为空时仍生成 `diagnosis-records/*.json`。
5. CSV 缺失时不会伪造 records 或进入截帧。
6. RPA 请求及波次行为无差异，既有 local 截帧测试保持通过。
