---
name: 千川视频点击峰值帧提取
description: 适用于需要从巨量千川素材分析、视频推直播单条诊断或千川 RPA JSON 中提取或审查 CLICK_COUNT 峰值帧，并使用本地视频或未来连接器支持运行的场景。
---

# 千川视频点击峰值帧提取

## 概览

提取时间线上第一个点击峰值，以及其后点击次数最高的三个峰值，并保存对应的 JPEG 帧和 JSON/Markdown 摘要。请使用随附的 Python 流程，确保 macOS 与 Windows 上的峰值和取帧规则一致且确定。

## 工作流程

1. 校验字段或准备连接器参数时，阅读 `references/rpa-output-contract.md`。
2. 在线 RPA 适配器尚未配置时，使用本地模式。
3. 创建隔离的 Python 3.9+ 环境并安装 `requirements.txt`。
4. 使用 RPA JSON、匹配的本地视频和输出目录运行 CLI。
5. 报告成功前，审查 `summary.json` 中的每个路径、对应 JPEG、`summary.md` 和所有警告。

## 本地运行

```bash
python scripts/extract_peak_frames.py \
  --input-json "/path/to/rpa-result.json" \
  --video "/path/to/material.mp4" \
  --output-dir "/path/to/output"
```

若生产环境单独管理 FFmpeg，请传入 `--ffmpeg-bin`。路径须作为独立参数传入；含空格的带引号路径可在 macOS 和 Windows 中使用。

## 选峰契约

- 仅读取指标为 `CLICK_COUNT` 的 `interaction_series` 记录。
- 排除时序的第一个和最后一个点。
- 将等值平台合并为一个峰值，并取其最早秒数。
- 先选择最早的峰值。
- 仅对其后的峰值按点击数降序、秒数升序排序；取前三个。
- 若其后峰值少于三个，返回可用帧并给出警告；绝不伪造峰值。

## 快速参考

| 需求 | 操作 |
| --- | --- |
| 默认 FFmpeg | 从 `requirements.txt` 安装 `imageio-ffmpeg` |
| 托管 FFmpeg | 添加 `--ffmpeg-bin "/path/to/ffmpeg"` |
| 本地结果 | 检查 `<output>/<material-id>/summary.json` |
| 在线来源 | 不要模拟；`--source rpa` 有意保持未配置状态 |
| 回归检查 | 运行 `python -m unittest tests/test_peak_frames.py -v` |

## 常见错误

- 不要将所有峰值一起排序；最早峰值必须在后续排序前固定。
- 不要将第一个或最后一个样本视为峰值。
- 其后峰值不足三个不应直接失败；应给出警告。
- 不要仅凭文件名报告成功；需检查摘要和 JPEG。
- 在实现 `scripts/rpa_adapter.py` 前，不要下载 `videoUrl`。
