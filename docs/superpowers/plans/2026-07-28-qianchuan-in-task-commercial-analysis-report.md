# 千川任务内商业分析与 Tab 报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让当前执行技能的模型在同一次任务内结合诊断事实和实际高光帧生成可验证商业分析，并渲染组合结论优先、支持素材 Tab 切换的静态 HTML 报告。

**Architecture:** 保持 RPA、抽帧和批次验收不变。诊断事实包新增高光帧及稳定证据标识；`final_report_lib.py` 先规范化 v1/v2 结论，再使用当前事实验证 v2 引用，最后渲染保留现有 Hero 风格的 Tab 报告。`SKILL.md` 编排当前模型看图、写 JSON 和最终渲染，不在 Python 中调用模型 API。

**Tech Stack:** Python 3.9+、标准库（`argparse`、`json`、`html`、`pathlib`、`unittest`）、内联 HTML/CSS/JavaScript。

---

## File Map

| Path | Responsibility |
| --- | --- |
| `scripts/prepare_diagnostic_analysis.py` | 新增并传递 `--peak-output-dir`。 |
| `scripts/diagnostic_analysis_lib.py` | 读取峰值帧摘要/JPEG，生成事实包高光帧索引和证据标识。 |
| `scripts/final_report_lib.py` | 校验 v2、兼容 v1、组装分析和渲染 Tab 报告。 |
| `prompts/diagnostic-analysis-prompt-v1.md` | 约束当前模型实看 JPEG，生成 v2。 |
| `SKILL.md` | 定义同任务内的分析和最终渲染闭环。 |
| `tests/test_diagnostic_analysis.py` | 测试帧索引、缺失告警和 CLI。 |
| `tests/test_final_report.py` | 测试 v2 引用、v1 兼容、章节和 Tab。 |
| `tests/test_skill_contract.py` | 测试技能的读图、v2、非草稿完成条件。 |

### Task 1: 为诊断事实包加入高光帧证据

**Files:**
- Modify: `tests/test_diagnostic_analysis.py`
- Modify: `scripts/diagnostic_analysis_lib.py`
- Modify: `scripts/prepare_diagnostic_analysis.py`

- [ ] **Step 1: 写入失败测试**

在 `DiagnosticAnalysisPublicationTests` 创建 `peak-frames/m-1/summary.json`，其 `frames` 包含 `first_peak` 和 `later_top_1`；只创建 `first_peak_0001s_3.jpg`。调用函数时传 `peak_output_dir=peak_dir`，断言：

```python
data_path, _prompt_path, analysis = prepare_diagnostic_analysis(
    top_materials, records_dir, output_dir, peak_output_dir=peak_dir
)
frames = analysis["highlights"]["materials"][0]["frames"]
self.assertEqual(frames[0]["evidenceRef"], "highlight:m-1:first_peak")
self.assertEqual(frames[0]["framePath"], str(peak_dir / "m-1" / "first_peak_0001s_3.jpg"))
self.assertIn("missing high-light JPEG for material m-1: later_top_1", analysis["warnings"])
self.assertEqual(json.loads(data_path.read_text(encoding="utf-8")), analysis)
```

新增 CLI 测试，使用 `--peak-output-dir`，断言返回 `0` 且 `diagnostic-analysis-data.json` 含 `highlights.materials`。

- [ ] **Step 2: 运行测试确认 RED**

```bash
.venv/bin/python -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisPublicationTests tests.test_diagnostic_analysis.DiagnosticAnalysisCliTests -v
```

Expected: FAIL，因为当前函数和 CLI 都不接受 `peak_output_dir`。

- [ ] **Step 3: 实现帧索引**

在 `diagnostic_analysis_lib.py` 添加以下帮助函数；它们只接受存在的 `summary.json` 和 JPEG，其他情况写明告警：

```python
def _highlight_evidence_ref(material_id, rank_type):
    return f"highlight:{material_id}:{rank_type}"


def _load_highlights(top_payload, peak_output_dir):
    result = {"materials": []}
    warnings = []
    for material in _selected_materials(top_payload)[0]:
        material_id = material["materialId"]
        summary_path = Path(peak_output_dir) / _safe_component(material_id) / "summary.json"
        frames = []
        if not summary_path.is_file():
            warnings.append(f"missing high-light summary for material {material_id}")
        else:
            summary = _read_json(summary_path, f"high-light summary for material {material_id}")
            for raw in summary.get("frames", []):
                rank_type = _text(raw.get("rankType")) if isinstance(raw, dict) else ""
                frame_path = summary_path.parent / _text(raw.get("framePath")) if isinstance(raw, dict) else summary_path.parent
                if not rank_type or not frame_path.is_file():
                    warnings.append(f"missing high-light JPEG for material {material_id}: {rank_type or 'unknown'}")
                    continue
                frames.append({"materialId": material_id, "rankType": rank_type, "durationSec": raw.get("durationSec"), "metricValue": raw.get("metricValue"), "framePath": str(frame_path), "evidenceRef": _highlight_evidence_ref(material_id, rank_type)})
        result["materials"].append({"materialId": material_id, "frames": frames})
    return result, warnings
```

Add this local `_safe_component()` to prevent material IDs from escaping their summary directory:

```python
def _safe_component(value):
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in str(value)
    )
    return cleaned.strip("_") or "unknown-material"
```

Add optional `peak_output_dir=None` to `prepare_diagnostic_analysis()`: when present, call `_load_highlights()`, assign `analysis["highlights"]`, and prepend its warnings. Add `parser.add_argument("--peak-output-dir")` to the CLI; `prepare_diagnostic_analysis(**vars(args))` already forwards it.

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
.venv/bin/python -m unittest tests.test_diagnostic_analysis -v
```

Expected: PASS；未传峰值目录的旧事实包结构不变，传入目录时有帧索引和精确告警。

- [ ] **Step 5: 提交事实包改动**

```bash
git add scripts/diagnostic_analysis_lib.py scripts/prepare_diagnostic_analysis.py tests/test_diagnostic_analysis.py
git commit -m "feat: include highlight evidence in diagnostic facts"
```

### Task 2: 实现 schema v2 与证据引用校验

**Files:**
- Modify: `tests/test_final_report.py`
- Modify: `scripts/final_report_lib.py`

- [ ] **Step 1: 写入失败测试**

定义两素材的高光帧和诊断事实，再加入下列 v2 fixture：

```python
V2_CONCLUSION = {
    "schemaVersion": 2,
    "overallConclusion": "24-30 岁人群对早起滋补表达反应更明显。",
    "batchInsights": [{"topic": "audience", "title": "点击人群集中", "conclusion": "点击层的 24-30 岁分桶领先。", "evidenceRefs": ["audience:CLICK_COUNT:age:24-30"]}],
    "materialAnalyses": [{
        "materialId": "m-1", "audienceConclusion": "点击层 24-30 岁突出。", "scriptConclusion": "价格优惠表达出现。",
        "highlightAnalyses": [{"frameRef": "highlight:m-1:first_peak", "visualObservation": "人物在产品近景旁口播。", "commercialInterpretation": "该画面与首个点击峰值同时出现。"}],
        "integratedConclusion": "保留产品近景开场。", "decision": "scale", "nextAction": "制作三条开场变体。",
        "evidenceRefs": ["audience:CLICK_COUNT:age:24-30", "script:m-1:价格优惠:降价/折扣", "highlight:m-1:first_peak"],
    }],
    "nextActions": [{"title": "扩展开场", "body": "测试三条变体。", "priority": "high"}], "warnings": [],
}
```

在该测试类定义一个使用当前 fixture 的辅助函数：

```python
def build_v2_report_data(self, conclusion):
    return build_final_report_data(
        self.top_payload,
        self.records_by_material,
        self.summaries_by_material,
        conclusion=conclusion,
        batch_verification={"valid": True, "errors": [], "warnings": []},
    )
```

断言 `self.build_v2_report_data(V2_CONCLUSION)` 为 `complete`。将任一引用替换成 `audience:SHOW_COUNT:age:24-30`、`highlight:m-9:first_peak` 或 `script:m-1:不存在:标签`，断言：

```python
with self.assertRaisesRegex(FinalReportError, "unknown evidence reference"):
    self.build_v2_report_data(invalid_conclusion)
```

保留 v1 测试，并断言历史输入仍为 `complete`。

- [ ] **Step 2: 运行测试确认 RED**

```bash
.venv/bin/python -m unittest tests.test_final_report.FinalReportConclusionTests tests.test_final_report.FinalReportDataTests -v
```

Expected: FAIL，因为当前只接受 schema v1 且不验证引用。

- [ ] **Step 3: 标准化并验证**

增加 `ALLOWED_INSIGHT_TOPICS = {"audience", "script", "highlight", "integrated"}`。保留当前 v1 校验逻辑为 `_normalize_v1_conclusion()`，并把它映射成内部统一结构；增加 `_normalize_v2_conclusion()`，验证所有字段、`topic`、`decision`、`priority`、文本非空、数组类型和素材分析 ID 不重复。

按以下方式生成仅来自当前批次的可用引用集合：

```python
def _known_evidence_refs(diagnostic, summaries_by_material):
    refs = set()
    for metric, metric_data in diagnostic["audience"]["metrics"].items():
        for dimension_key, dimension in metric_data["dimensions"].items():
            refs.update(f"audience:{metric}:{dimension_key}:{bucket['bucket']}" for bucket in dimension["buckets"])
    for material in diagnostic["scripts"]["materials"]:
        refs.update(f"script:{material['materialId']}:{label['level1']}:{label['level2']}" for label in material["labels"])
    for material_id, summary in summaries_by_material.items():
        refs.update(f"highlight:{material_id}:{frame['rankType']}" for frame in summary.get("frames", []))
    return refs
```

`normalize_conclusion_analysis()` 按版本派发。`build_final_report_data()` 已持有 `diagnostic` 和 `summaries_by_material`，在构建输出前对 v2 中每个 `evidenceRefs` 和 `frameRef` 做集合成员检查；未知值报 `FinalReportError(f"unknown evidence reference: {reference}")`。对每条有帧的素材要求恰有一条 v2 分析；缺帧素材不要求分析，以维持 `partial` 工作流。

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
.venv/bin/python -m unittest tests.test_final_report -v
```

Expected: PASS；v2 错误引用被拒绝，v1 可继续渲染，缺帧素材保持 `partial`。

- [ ] **Step 5: 提交结论契约改动**

```bash
git add scripts/final_report_lib.py tests/test_final_report.py
git commit -m "feat: validate commercial analysis v2 evidence"
```

### Task 3: 渲染组合结论优先的静态 Tab 报告

**Files:**
- Modify: `tests/test_final_report.py`
- Modify: `scripts/final_report_lib.py`

- [ ] **Step 1: 写入失败测试**

以两素材 v2 结论生成 HTML，加入：

```python
self.assertNotIn("01｜组合总览", html_text)
for heading in ("01｜TOP 素材组合结论", "02｜人群洞察", "03｜脚本洞察", "04｜高光帧洞察", "05｜单素材深度分析", "06｜下一轮策略", "07｜数据质量与方法说明"):
    self.assertIn(heading, html_text)
self.assertIn('role="tablist"', html_text)
self.assertIn('role="tab"', html_text)
self.assertIn('role="tabpanel"', html_text)
self.assertIn('data-material-id="m-1"', html_text)
self.assertIn('data-material-id="m-2"', html_text)
self.assertIn("function activateMaterialTab", html_text)
self.assertIn("ArrowRight", html_text)
self.assertIn("Home", html_text)
```

再断言第一排名素材的按钮含 `aria-selected="true"`，第二条为 `false`，HTML 不含旧 `.metrics` 输出。

- [ ] **Step 2: 运行测试确认 RED**

```bash
.venv/bin/python -m unittest tests.test_final_report.FinalReportRenderTests -v
```

Expected: FAIL，因为当前 HTML 含组合总览和纵向全部素材卡。

- [ ] **Step 3: 组装渲染数据**

用 `materialAnalyses` 按 `materialId` 替换 `_recommendations_by_material()` 的单一建议映射。每条素材卡保存 `analysis`、`highlightFrames` 和 `isAnalyzable=bool(highlightFrames)`；`defaultMaterialId` 是按 rank 排序后首个可分析素材。报告数据保留旧 `overview` JSON 字段，但不渲染它。

将组合洞察按 `topic` 分到 `audienceInsights`、`scriptInsights`、`highlightInsights` 和 `integratedInsights`。材料卡的正文固定渲染三块：`人群与脚本证据`、`高光帧观察`、`综合判断与下一步`。

- [ ] **Step 4: 重写 HTML 内容区与 Tab 切换**

保留现有 Hero、配色 token、封面和响应式基础样式。移除 `01｜组合总览` 与 `.metrics`，按测试中的七个标题输出章节。为每个素材输出一个 tab 和 panel：

```python
f'<button type="button" role="tab" id="tab-{safe_id}" aria-controls="panel-{safe_id}" aria-selected="{str(selected).lower()}" tabindex="{0 if selected else -1}" data-material-id="{safe_id}">#{rank}</button>'
f'<article role="tabpanel" id="panel-{safe_id}" aria-labelledby="tab-{safe_id}" data-material-id="{safe_id}" {"" if selected else "hidden"}>{material_panel_html}</article>'
```

在静态 HTML 内嵌以下无依赖 JavaScript，并在 DOMContentLoaded 后绑定所有 `[data-material-tabs]`：

```javascript
function activateMaterialTab(tab) {
  const container = tab.closest('[data-material-tabs]');
  container.querySelectorAll('[role="tab"]').forEach((item) => {
    const selected = item === tab;
    item.setAttribute('aria-selected', String(selected));
    item.tabIndex = selected ? 0 : -1;
  });
  container.querySelectorAll('[role="tabpanel"]').forEach((panel) => {
    panel.hidden = panel.dataset.materialId !== tab.dataset.materialId;
  });
  tab.focus();
}
```

键盘处理 `ArrowLeft`/`ArrowRight` 循环当前 tabs，`Home` 选择首个，`End` 选择末个，并调用 `preventDefault()`。每个 panel 保留现有四帧网格，图片继续通过 `_html_asset_src()` 相对化。

- [ ] **Step 5: 运行测试确认 GREEN**

```bash
.venv/bin/python -m unittest tests.test_final_report -v
```

Expected: PASS；HTML 无组合总览，保留参考报告的 Hero/视觉语言，且所有素材可离线 Tab 切换。

- [ ] **Step 6: 提交 Tab 报告改动**

```bash
git add scripts/final_report_lib.py tests/test_final_report.py
git commit -m "feat: render tabbed material commercial report"
```

### Task 4: 把同任务模型读图写入技能契约

**Files:**
- Modify: `tests/test_skill_contract.py`
- Modify: `prompts/diagnostic-analysis-prompt-v1.md`
- Modify: `SKILL.md`

- [ ] **Step 1: 写入失败的契约测试**

在 `SkillContractTests` 添加：

```python
def test_final_report_requires_in_task_visual_analysis_and_v2_output(self):
    skill = SKILL_PATH.read_text(encoding="utf-8")
    prompt = (ROOT / "prompts" / "diagnostic-analysis-prompt-v1.md").read_text(encoding="utf-8")
    self.assertIn("当前执行技能的模型", skill)
    self.assertIn("实际查看", skill)
    self.assertIn("--peak-output-dir", skill)
    self.assertIn("--analysis-json", skill)
    self.assertIn("schemaVersion\": 2", prompt)
    for metric in ("SHOW_COUNT", "CLICK_COUNT", "TRANSACTION_AMOUNT", "JPEG"):
        self.assertIn(metric, prompt)
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
.venv/bin/python -m unittest tests.test_skill_contract.SkillContractTests -v
```

Expected: FAIL，因为当前内容仍要求外部分析环境。

- [ ] **Step 3: 更新提示词与技能**

将提示词改为：当前执行技能的模型逐张实际查看 `highlights.materials[*].frames[*].framePath` 指向的 JPEG；仅用事实包和 JPEG；人群结论必须标明三个指标层之一；输出 `schemaVersion: 2`；`evidenceRefs` 和 `frameRef` 必须逐字复制事实包给出的标识。

将 `SKILL.md` 的最终报告命令更新为：

```bash
python scripts/prepare_diagnostic_analysis.py \
  --top-materials-json "/path/to/run/top_materials_rpa_ready.json" \
  --diagnosis-records-dir "/path/to/run/diagnosis-records" \
  --peak-output-dir "/path/to/run/peak-frames" \
  --output-dir "/path/to/run/diagnostic-analysis"
```

紧接命令要求当前模型读事实、实际看图、写 v2 JSON，在校验失败时修正，然后必带 `--analysis-json` 调用渲染器。草稿模式只能作为调试说明，不得被描述为完成。

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
.venv/bin/python -m unittest tests.test_skill_contract -v
```

Expected: PASS；技能锁定读图、三层人群、v2 和最终渲染步骤。

- [ ] **Step 5: 提交技能契约改动**

```bash
git add SKILL.md prompts/diagnostic-analysis-prompt-v1.md tests/test_skill_contract.py
git commit -m "docs: require in-task visual commercial analysis"
```

### Task 5: 全量回归与端到端验收

**Files:**
- Modify only if verification exposes a defect in files above.

- [ ] **Step 1: 运行全量自动化验证**

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m py_compile scripts/*.py
```

Expected: 两条命令均返回 `0`。

- [ ] **Step 2: 验收端到端报告**

在测试中用 `TemporaryDirectory` 构造一条包含人群、脚本和两个 JPEG 帧的素材，准备 v2 JSON 后运行：

```bash
.venv/bin/python scripts/prepare_diagnostic_analysis.py --top-materials-json "$RUN_DIR/top_materials.json" --diagnosis-records-dir "$RUN_DIR/diagnosis-records" --peak-output-dir "$RUN_DIR/peak-frames" --output-dir "$RUN_DIR/diagnostic-analysis"
.venv/bin/python scripts/render_final_report.py --top-materials-json "$RUN_DIR/top_materials.json" --peak-output-dir "$RUN_DIR/peak-frames" --diagnosis-records-dir "$RUN_DIR/diagnosis-records" --analysis-json "$RUN_DIR/diagnostic-analysis/diagnostic-ai-analysis.json" --output-dir "$RUN_DIR/final-report"
```

Expected: 数据文件为 `complete`；HTML 含七个章节、`role="tab"` 和相对高光帧路径，且不含 `01｜组合总览`。

- [ ] **Step 3: 检查并提交验证修复**

```bash
git diff --check
git status --short
```

若 Task 5 未修改文件，不创建空提交。若修复验证问题：

```bash
git add SKILL.md prompts scripts tests
git commit -m "fix: complete commercial analysis report flow"
```

## Plan Self-Review

- **Spec coverage:** Task 1 实现帧证据；Task 2 实现 v2 与引用校验；Task 3 移除总览、保留报告视觉骨架并加入 Tab；Task 4 锁定模型内看图闭环；Task 5 覆盖回归与端到端交付。
- **Placeholder scan:** 每项改动均给出目标文件、字段/接口、失败断言、实现行为和验证命令，没有 TBD、TODO 或未定义的“适当处理”。
- **Type consistency:** 高光帧引用始终为 `highlight:<materialId>:<rankType>`，人群引用为 `audience:<metric>:<dimensionKey>:<bucket>`，脚本引用为 `script:<materialId>:<level1>:<level2>`，新任务结论始终为 `schemaVersion: 2`。
