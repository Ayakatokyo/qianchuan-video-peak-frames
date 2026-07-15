# Skill RED Baseline

## Evaluation prompt

Analyze /Users/shuzida/Desktop/FDE客户/小仙炖/千川短视频/千川-素材分析-视频推直播单条诊断.md.
Design the exact local command and deterministic processing rules you would use to take the first chronological CLICK_COUNT peak and the later top three peaks by click count, then extract JPEG frames from a local MP4 on both macOS and Windows. Do not modify the workspace. State dependencies, tie/plateau behavior, FFmpeg lookup, output files, and failure behavior.

## Baseline output

<!-- BEGIN VERBATIM BASELINE OUTPUT -->
Read-only analysis complete. Evidence: doc lines 164/171/173/174 define `recordType=interaction_series`, `interactionMetric=CLICK_COUNT`, `durationSec`, `metricValue`; lines 308 and 319-334 show multiple time points concept/sample. The doc does NOT define peak semantics.

Recommended approach (vs jq+shell or separate Bash/PowerShell, which drift cross-platform): one Python 3.10+ stdlib script that selects peaks and invokes FFmpeg with argv (never shell=True). Exact user-facing commands:
macOS:
`python3 "/absolute/path/extract_click_peak_frames.py" --input-json "/absolute/path/connector-output.json" --material-id "7647499280192372763" --video "/absolute/path/video.mp4" --output-dir "/absolute/path/peak_frames"`
Windows PowerShell:
`py -3 "C:\absolute\path\extract_click_peak_frames.py" --input-json "C:\absolute\path\connector-output.json" --material-id "7647499280192372763" --video "C:\absolute\path\video.mp4" --output-dir "C:\absolute\path\peak_frames"`
Optional exact override on either: `--ffmpeg <absolute executable>`.

Dependencies: readable UTF-8 JSON whose root is the connector's documented List/JSON array; readable local MP4; Python >=3.10 (stdlib only); FFmpeg (ffmpeg executable; ffprobe not necessary if post-extraction no-frame checking is used). Suggested installs: macOS `brew install ffmpeg`; Windows `winget install --id Gyan.FFmpeg -e`.

Deterministic data rules:
1. Parse JSON with `json.load(..., parse_int=Decimal, parse_float=Decimal)`; root MUST be list. Filter exact: `recordType == "interaction_series"`, `interactionMetric == "CLICK_COUNT"`, `materialId == CLI --material-id`. Do not fall back to nested Dimensions/Metrics, because top-level normalized fields are documented.
2. Require `durationSec` and `metricValue` to be JSON numbers (not bool/string), finite, >=0. Sort ascending by durationSec. Duplicate durationSec is an ambiguity => fail, even if values match. Require at least 2 samples.
3. Plateau: collapse each maximal adjacent run (in duration-sorted observations) having exactly equal Decimal metricValue. Keep `plateauStart`, `plateauEnd`, value. Representative/seek time is plateauStart (earliest time), so "first chronological" remains literal.
4. A run is a local peak iff it has >=1 neighboring run and its value is strictly greater than every existing neighbor. Thus internal runs must exceed both sides; start/end runs may be one-sided peaks; a single point or wholly constant series has no peak.
5. `first` = peak with smallest plateauStart. `later` = all remaining peaks (necessarily after first plateau); select exactly 3 ordered by `(-metricValue, plateauStart)`—click-count descending; equal counts break toward earlier time. A plateau is one peak and cannot occupy multiple slots.
6. Require first + 3 later peaks. If fewer, strict failure (do not silently emit fewer or duplicate frames).

Per-frame FFmpeg argv (same on both OS; Python substitutes resolved executable/paths/time):
`[FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", VIDEO, "-ss", DECIMAL_SECONDS, "-map", "0:v:0", "-frames:v", "1", "-an", "-sn", "-dn", "-q:v", "2", "-f", "image2", OUTPUT_JPG]`
Putting `-ss` after `-i` makes FFmpeg decode/discard up to the time and choose the first decoded video frame at/after that timestamp (frame-accurate rather than keyframe-only fast seeking). Pass argv directly. Do not clamp out-of-range timestamps or reuse nearest frame. Semantic frame selection is deterministic; byte-identical JPEGs across machines require pinning the same FFmpeg version/build/codec.

FFmpeg lookup order: (1) `--ffmpeg`, which must resolve to an executable file; (2) `shutil.which("ffmpeg")`; (3) macOS `/opt/homebrew/bin/ffmpeg`, then `/usr/local/bin/ffmpeg`; (4) Windows `%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe`, `%ChocolateyInstall%\bin\ffmpeg.exe` (default `%ProgramData%\chocolatey\bin\ffmpeg.exe`), then `C:\ffmpeg\bin\ffmpeg.exe`. No recursive filesystem search. Run `<resolved> -version` and require exit 0.

Outputs (fixed stable names, selection metadata held in manifest):
`01_first_chronological.jpg`
`02_later_top01.jpg`
`03_later_top02.jpg`
`04_later_top03.jpg`
`peaks.json` (for each: role, clickRank where applicable, durationSec/seekTimeSec as canonical decimal string, metricValue, plateauStartSec, plateauEndSec; plus materialId, input/video canonical paths, and resolved ffmpeg/version).
Use output sibling staging dir `<output-dir>.tmp`; require both final and staging dirs absent. Extract all 4 there, require each subprocess exit 0 and each JPEG exists/nonempty (optionally check JPEG SOI/EOI), write UTF-8 `peaks.json` with sorted keys/newline, then rename staging dir atomically to final. On any exception/interrupt delete staging and exit nonzero; never leave partial final output or overwrite an existing output dir.

Suggested exact exit behavior: 2 = CLI/input/data validation; 3 = fewer than four qualifying peaks; 4 = FFmpeg lookup/preflight/extraction/no frame; 5 = output collision/I/O. All errors to stderr and no final output directory.

I ran in-memory synthetic checks: plateau merge, click-count tie sorted earlier first, one-sided endpoint, all-constant, and singleton; 5/5 passed. No workspace files modified.
<!-- END VERBATIM BASELINE OUTPUT -->

## Observable gaps against the approved design

- Boundary points: the approved design excludes boundary points, while the baseline treats start and end runs as eligible one-sided peaks.
- Sparse later peaks: the approved design emits all available frames with a warning when fewer than three later peaks exist, while the baseline requires exactly three later peaks and fails otherwise.
- Runtime and dependencies: the approved design targets Python 3.9+ with `imageio-ffmpeg`, while the baseline requires Python 3.10+ and a separately installed FFmpeg executable.
- Duplicate durations: the approved design deduplicates identical duplicate `(durationSec, metricValue)` observations and rejects only conflicting duplicates, while the baseline rejects every duplicate duration even when values match.
- FFmpeg lookup: the approved lookup order is `--ffmpeg-bin`, `IMAGEIO_FFMPEG_EXE`, `imageio_ffmpeg.get_ffmpeg_exe()`, then system `PATH`; the baseline uses `--ffmpeg`, then system `PATH`, followed by platform-specific paths, and omits the imageio lookup sources.
- Output contract: the approved design produces deterministic named JPEGs plus `summary.json` and `summary.md`; the baseline uses different JPEG names, emits `peaks.json`, and omits the Markdown summary.
- Execution boundary: the approved design makes local mode primary and reserves an explicit unconfigured online RPA adapter; the baseline does not describe that adapter boundary.

## Skill-enabled output

<!-- BEGIN VERBATIM SKILL-ENABLED OUTPUT -->
该 Markdown 是连接器字段契约，不是可直接处理的完整数据。提取时必须提供连接器导出的完整 JSON 列表，以及对应的本地视频；文档中的单个 `CLICK_COUNT` 示例点无法形成峰值。

**本地调用**

macOS：

```bash
cd "/Users/shuzida/Desktop/FDE客户/小仙炖/qianchuan-video-peak-frames"

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt

.venv/bin/python scripts/extract_peak_frames.py \
  --source local \
  --input-json "/absolute/path/rpa-result.json" \
  --video "/absolute/path/material.mp4" \
  --output-dir "/absolute/path/output"
```

使用受管 FFmpeg 时增加：

```bash
--ffmpeg-bin "/absolute/path/to/ffmpeg"
```

Windows PowerShell 等价调用：

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

.\.venv\Scripts\python.exe .\scripts\extract_peak_frames.py `
  --source local `
  --input-json "C:\absolute path\rpa-result.json" `
  --video "C:\absolute path\material.mp4" `
  --output-dir "C:\absolute path\output" `
  --ffmpeg-bin "C:\ffmpeg\bin\ffmpeg.exe"
```

路径必须作为独立参数传入；包含空格时加引号。当前 `--source rpa` 明确未配置，不能用它调用连接器或自动下载 `videoUrl`。

**峰值规则**

1. JSON 根节点必须是列表。
2. 只读取同时满足以下条件的记录：
   - `recordType == "interaction_series"`
   - `interactionMetric == "CLICK_COUNT"`
3. `durationSec` 必须是有限、非负数；`metricValue` 必须是有限数。
4. 数据按 `durationSec` 排序。同秒同值的重复记录合并；同秒不同值直接报错。
5. `CLICK_COUNT` 记录中若出现多个非空 `materialId`，直接报错。
6. 连续等值平台只保留最早时间点，然后以严格大于前后相邻采样值判断局部峰值。首、尾平台不参与峰值判断。
7. 先固定时间最早的局部峰值，标记为 `first_peak`。它不会与其他峰值比较点击量。
8. 只在它之后的峰值中，按“点击量降序、秒数升序”排序，取前三个，标记为 `later_top_1` 至 `later_top_3`。
9. 后续峰值不足三个时只输出实际存在的帧，并在摘要中警告，绝不补造。
10. 所有选中时间必须满足 `0 <= durationSec < 视频实际时长`，否则提取前即失败。

例如峰值依次为 `(1s,2)`、`(3s,9)`、`(5s,7)`、`(7s,8)`，结果顺序是 `1s`、`3s`、`7s`、`5s`。

**FFmpeg 跨平台行为**

FFmpeg 按以下优先级解析：

1. `--ffmpeg-bin`
2. 环境变量 `IMAGEIO_FFMPEG_EXE`
3. `imageio-ffmpeg` 自带的可执行文件
4. 系统 `PATH` 中的 `ffmpeg`

前两种路径必须指向已有普通文件。代码通过参数数组调用 FFmpeg，不经过 shell，因此 macOS 和 Windows 的含空格路径行为一致。

程序先用 FFmpeg 探测视频时长，再按选中秒数执行单帧 JPEG 提取，质量参数为 `-q:v 2`。每个文件先写入临时路径，随后检查：

- 文件存在且非空
- JPEG 起止标记正确
- FFmpeg 能重新解码该 JPEG

全部通过后才原子替换正式文件。输入错误返回码为 `2`，FFmpeg 错误为 `3`，未配置的 RPA 模式为 `4`。

**输出**

结果位于：

```text
<output-dir>/<material-id>/
```

没有 `materialId` 时使用视频文件名。目录名会清理不适合路径的字符。

典型文件如下：

```text
first_peak_0001s_2.jpg
later_top_1_0003s_9.jpg
later_top_2_0007s_8.jpg
later_top_3_0005s_7.jpg
summary.json
summary.md
```

小数秒和小数指标以 `p` 替代小数点，例如 `0012p5s`。`summary.json` 包含素材 ID、输入路径、视频路径、选择规则、每帧的类型/秒数/点击量/文件名及全部警告；`summary.md` 提供同一结果的表格视图。

**结果验证**

成功运行时终端会打印：

```text
result_dir=...
summary_json=...
summary_md=...
```

不能只凭文件名判断成功。应逐项确认：

1. 命令退出码为 `0`。
2. 打开 `summary.json`，核对 `first_peak` 确实是时间最早的局部峰值。
3. 核对 `later_top_*` 只来自更晚峰值，并满足点击量降序、同值秒数升序。
4. 将每个 `framePath` 相对于结果目录解析，确认文件存在、非空，并实际打开检查画面。
5. 阅读 `summary.md` 和 `warnings`；后续峰值不足三个属于带警告的有效结果。
6. 需要验证工具自身回归时运行：

```bash
.venv/bin/python -m unittest tests/test_peak_frames.py -v
```
<!-- END VERBATIM SKILL-ENABLED OUTPUT -->

## Comparison

- Local mode: PASS. The response requires a full local RPA JSON list and matching local video, and does not treat the Markdown sample as runnable data.
- Peak contract: PASS. It excludes endpoint plateaus, collapses equal plateaus to the earliest second, fixes the chronological first peak, and ranks only later peaks by clicks then time.
- Sparse peaks: PASS. It returns available later frames with a warning and never fabricates frames.
- Cross-platform FFmpeg: PASS. It gives macOS and Windows commands, handles spaced paths, and states the four-level FFmpeg resolution order.
- Online boundary: PASS. It identifies `--source rpa` as intentionally unconfigured and refuses to simulate connector or `videoUrl` behavior.
- Outputs: PASS. It describes deterministic JPEG names plus `summary.json` and `summary.md` with portable frame filenames and warnings.
- Verification: PASS. It requires checking exit status, summary semantics, every listed JPEG, Markdown, warnings, and the regression suite.
- New rationalizations: none observed. No SKILL.md refinement was required.
