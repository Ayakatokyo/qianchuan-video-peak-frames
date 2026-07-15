# 千川视频点击峰值画面提取技能设计

## 目标

创建项目内技能 `qianchuan-video-peak-frames`，从巨量千川单条素材诊断 RPA 输出中读取“整体点击次数”互动时序，选择时间线上第一个峰值及其后点击次数最高的三个峰值，并从对应视频秒数截取 JPEG 画面。

当前版本必须可使用本地 RPA JSON 和本地视频运行，同时保留尚未完成的线上 RPA 连接器入口。实现须兼容 macOS 与 Windows。

## 已确认决策

| 主题 | 决策 |
| --- | --- |
| 首个峰值 | 时间线上最早出现的局部峰值 |
| 后续峰值 | 只在首个峰值之后选择，按点击次数降序取 TOP 3 |
| 平台峰 | 连续相同峰值合并为一个峰，取最早秒数 |
| 边界点 | 视频起点和终点不参与局部峰值判断 |
| 并列排序 | 点击次数相同时按秒数升序 |
| 输出 | 独立 JPEG 画面，以及 JSON、Markdown 摘要 |
| 当前入口 | 本地 RPA JSON + 本地视频文件 |
| 未来入口 | 自动调用 RPA 并下载 `videoUrl` 后复用本地处理管线 |
| 截帧方案 | 跨平台 Python + FFmpeg |
| FFmpeg 来源 | 支持显式路径、环境变量、`imageio-ffmpeg` 和系统 PATH |
| 技能位置 | `/Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames` |

## 范围

### 本期范围

- 解析 RPA `List[Dict]` JSON。
- 筛选 `recordType=interaction_series` 且 `interactionMetric=CLICK_COUNT` 的时序记录。
- 按确定性规则识别和选择峰值。
- 从本地视频按峰值秒数精确截取 JPEG。
- 生成机器可读 JSON 和人工可读 Markdown 摘要。
- 提供尚未接通的 RPA 适配器接口，并返回明确的未配置错误。
- 提供跨平台 CLI、自动化测试和 Codex Skill 元数据。

### 非本期范围

- 实现或修改 `rpa.conn.juliang.qc.material.analysis.video.live.diagnosis`。
- 绕过千川登录、签名 URL、Cookie 或授权限制。
- 在连接器完成前自动下载线上视频。
- 对峰值做平滑、显著性阈值、最小峰距或业务主观过滤。
- 生成视频片段、拼图或画面内容理解报告。

## 技能结构

```text
qianchuan-video-peak-frames/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── extract_peak_frames.py
│   ├── peak_frame_lib.py
│   └── rpa_adapter.py
├── references/
│   └── rpa-output-contract.md
├── tests/
│   └── test_peak_frames.py
└── docs/superpowers/specs/
    └── 2026-07-15-qianchuan-video-peak-frames-design.md
```

职责边界：

- `SKILL.md`：说明触发条件、输入选择、运行步骤和结果校验。
- `extract_peak_frames.py`：解析 CLI 参数并编排本地或 RPA 输入源。
- `peak_frame_lib.py`：提供无 UI、可单元测试的解析、峰值选择、FFmpeg 定位、截帧和摘要生成能力。
- `rpa_adapter.py`：定义线上调用接口；连接器完成前只返回可识别的未配置错误。
- `rpa-output-contract.md`：只保留本技能使用的 RPA 字段和约束，不复制整份连接器文档。

## 数据流

### 本地模式

```text
RPA JSON + 本地视频
  → 校验输入
  → 提取 CLICK_COUNT 时序
  → 识别局部峰值
  → 选择首个峰值和后续 TOP 3
  → 校验峰值秒数未超过视频时长
  → FFmpeg 精确截帧
  → 原子写入 JPEG、summary.json、summary.md
```

### 线上预留模式

```text
material_id + date_range
  → rpa_adapter 调用连接器
  → 获得 RPA JSON
  → 下载 videoUrl 为临时视频
  → 复用本地模式处理管线
```

连接器未完成时，线上模式必须以非零状态退出并说明“RPA 入口尚未配置”，不得生成模拟结果或空摘要。

## 输入契约

本地模式必填：

- `--input-json`：RPA 返回的 JSON 文件。
- `--video`：与 RPA 素材对应的本地视频文件。
- `--output-dir`：输出根目录。

可选：

- `--ffmpeg-bin`：用户维护的 FFmpeg 可执行文件。

线上预留模式：

- `--source rpa`
- `--material-id`
- `--date-range-type`
- 当周期为 `CUSTOM` 时要求开始、结束日期。

本技能只依赖以下 RPA 字段：

- 时序：`recordType`、`interactionMetric`、`durationSec`、`metricValue`、`materialId`。
- 未来线上视频：`recordType=material_info` 的 `videoUrl`、`videoDuration`。

## 峰值算法

1. 筛选 `recordType=interaction_series` 且 `interactionMetric=CLICK_COUNT` 的记录。
2. 将 `durationSec` 和 `metricValue` 转为有限数值，拒绝空值、NaN 和无穷值。
3. 按 `durationSec` 升序排列。相同秒数存在不同点击值时视为输入冲突并失败，不静默猜测。
4. 排除第一个和最后一个时序点。
5. 普通点仅在值严格高于左右相邻值时算峰值。
6. 对连续相同值形成的平台，只有当平台值严格高于平台左右两侧值时才算一个峰值；峰值秒数取平台起点。
7. 按秒数升序取第一个峰值，标记为 `first_peak`。
8. 仅保留其后的峰值，按 `metricValue` 降序、`durationSec` 升序排列，取前三个并标记为 `later_top_1` 至 `later_top_3`。
9. 后续峰值不足三个时输出已有结果，并在摘要写入结构化警告。
10. 完全没有峰值时失败，不输出空报告。

## 截帧方案

FFmpeg 可执行文件按以下顺序解析：

1. `--ffmpeg-bin`
2. `IMAGEIO_FFMPEG_EXE`
3. `imageio_ffmpeg.get_ffmpeg_exe()`
4. 系统 `PATH` 中的 `ffmpeg`

使用参数数组调用子进程，禁止依赖 shell 字符串拼接，以兼容 Windows 路径并避免转义问题。截帧采用输入后精确寻址，解码到目标时间再输出一帧，避免只跳到最近关键帧。每个峰值输出一张 JPEG。

本地视频必须存在且可解码。若峰值秒数不小于视频时长，则任务失败，避免输出尾帧或错误画面。

## 输出契约

输出目录按素材 ID 分组；素材 ID 缺失时使用输入视频文件名的安全化主干名。

```text
output/7647499280192372763/
├── first_peak_0007s_18.jpg
├── later_top_1_0011s_13.jpg
├── later_top_2_0013s_10.jpg
├── later_top_3_0032s_7.jpg
├── summary.json
└── summary.md
```

`summary.json` 顶层至少包含：

- `materialId`
- `inputJsonPath`
- `videoPath`
- `selectionRule`
- `frames`
- `warnings`

每个 `frames` 元素至少包含：

- `rankType`
- `durationSec`
- `metricValue`
- `requestedTimestampSec`
- `framePath`

`summary.md` 展示相同信息，并按首个峰值、后续 TOP 3 的业务语义排序，而不是按最终时间顺序重新排列。

所有 JPEG 成功后再写摘要。摘要通过临时文件原子替换，截帧失败时不得留下看似成功的完整摘要。

## CLI 示例

本地模式：

```bash
python scripts/extract_peak_frames.py \
  --input-json ./rpa-result.json \
  --video ./material.mp4 \
  --output-dir ./output
```

显式指定 FFmpeg：

```bash
python scripts/extract_peak_frames.py \
  --input-json ./rpa-result.json \
  --video ./material.mp4 \
  --output-dir ./output \
  --ffmpeg-bin /path/to/ffmpeg
```

线上预留模式：

```bash
python scripts/extract_peak_frames.py \
  --source rpa \
  --material-id 7647499280192372763 \
  --date-range-type LAST_7_DAYS \
  --output-dir ./output
```

## 错误处理

- JSON 不是列表或记录结构无效：失败并指出输入文件。
- 缺少有效 `CLICK_COUNT` 时序：失败，不生成空报告。
- 同一秒出现冲突点击值：失败并列出冲突秒数。
- 本地视频不存在或无法解码：失败并说明路径。
- 没有局部峰值：失败并说明数据不满足算法。
- 后续峰值不足三个：成功输出已有画面并写警告。
- 峰值秒数越过视频时长：失败并报告峰值秒数与视频时长。
- FFmpeg 不可用：失败并提示安装 `imageio-ffmpeg` 或传入 `--ffmpeg-bin`。
- 线上适配器未配置：以专用错误失败，不影响本地模式。

所有用户错误通过清晰的 stderr 文本和非零退出码表达；成功时 stdout 输出结果目录和摘要路径。

## 测试策略

### 代码 TDD

先编写并运行失败测试，再实现最小代码。覆盖：

- 普通峰值。
- 平台峰合并和最早秒选择。
- 起点、终点排除。
- 首个峰值与后续 TOP 3 的不同排序语义。
- 点击次数并列时按秒数排序。
- 后续峰值不足三个。
- 无峰值、无点击时序、冲突秒数、非法数值。
- FFmpeg 四级解析顺序。
- 视频路径错误、时间戳越界和线上入口未配置。

### 截帧集成测试

1. 使用 FFmpeg 生成每秒颜色不同的短视频。
2. 构造峰值秒数已知的 RPA JSON fixture。
3. 执行完整 CLI。
4. 验证 JPEG 数量、文件名、摘要字段和警告。
5. 使用 FFmpeg 将 JPEG 解码为原始像素，检查中心像素颜色，证明画面来自目标秒数。

### 技能 TDD

- 创建技能前，用独立执行场景记录无技能基线行为及失败点。
- 创建技能后，用相同场景验证代理选择正确入口、传递正确参数并检查结果。
- 运行 Skill `quick_validate.py`。
- 验证 `SKILL.md`、`agents/openai.yaml`、参考契约和 CLI 保持一致。

### 跨平台边界

实现不得依赖 Bash、macOS 路径分隔符或 `shell=True`。当前开发环境执行 macOS 端到端截帧测试；Windows 路径与解析逻辑纳入自动化测试。真正的 Windows 端到端验证需要后续在 Windows 机器或 Windows CI Runner 上执行，交付时必须明确该限制。

## 验收标准

- 给定有效本地 JSON 和视频，按规则输出首个峰值及后续 TOP 3 对应 JPEG。
- 平台峰、边界点和并列排序行为与本设计一致。
- 输出 JSON 与 Markdown 对峰值秒数、点击次数、画面路径描述一致。
- 集成测试证明截取的是目标秒数画面。
- 缺少 FFmpeg、无峰值、越界和未配置线上入口均返回明确错误。
- 单元测试、集成测试和 Skill 结构校验全部通过。
- 本地模式不依赖未完成的 RPA；未来线上适配器不修改峰值和截帧核心逻辑即可接入。
