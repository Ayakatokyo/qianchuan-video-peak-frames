# 千川素材商业报告 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 TOP50 + 单素材 RPA 截帧链路后，生成可视化 `report.html` 与可追溯 `analysis.json`，先覆盖素材级曝光人群聚合和高光帧商业展示。

**Architecture:** 保持现有峰值截帧摘要不变，新增独立商业报告层读取 `top_materials.json`、RPA 原始诊断记录目录和截帧输出目录。RPA 批量截帧 CLI 增加可选诊断记录持久化参数，为报告层提供 `crowd_analysis` 原始输入。

**Tech Stack:** Python 标准库、`unittest`、现有 `scripts/extract_peak_frames.py`、新增报告库与 CLI。

---

### Task 1: 人群聚合与报告生成测试

**Files:**
- Create: `tests/test_commercial_report.py`
- Create: `scripts/commercial_report_lib.py`
- Create: `scripts/generate_commercial_report.py`

- [ ] **Step 1: Write failing aggregation tests**

Create `tests/test_commercial_report.py` with tests that import `aggregate_crowd_analysis` and verify:

```python
def test_aggregates_show_count_crowd_records_by_dimension_and_bucket(self):
    records_by_material = {
        "764": [
            crowd_record("764", "age", "18-23", 100),
            crowd_record("764", "age", "24-30", 300),
            crowd_record("764", "interest", "滋补养生", 80),
            {**crowd_record("764", "age", "ignored", 999), "displayMetric": "CLICK_COUNT"},
        ],
        "765": [
            crowd_record("765", "age", "18-23", 200),
            crowd_record("765", "interest", "滋补养生", 120),
        ],
    }

    result = aggregate_crowd_analysis(records_by_material, ["764", "765"])

    age = result["dimensions"]["age"]
    self.assertEqual(age["totalMetricValue"], 600.0)
    self.assertEqual(age["buckets"][0]["bucket"], "24-30")
    self.assertEqual(age["buckets"][0]["metricValue"], 300.0)
    self.assertAlmostEqual(age["buckets"][0]["metricPercentage"], 50.0)
```

Also verify missing material IDs are recorded when a selected material has no valid `SHOW_COUNT` crowd record.

- [ ] **Step 2: Run aggregation tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_commercial_report -v`

Expected: FAIL with `ModuleNotFoundError` or missing `aggregate_crowd_analysis`.

- [ ] **Step 3: Implement minimal aggregation library**

Create `scripts/commercial_report_lib.py` with:

```python
class CommercialReportError(RuntimeError):
    pass

def aggregate_crowd_analysis(records_by_material, selected_material_ids):
    ...
```

The function must:

- only accept `recordType == "crowd_analysis"` and `displayMetric == "SHOW_COUNT"`;
- group by `dimensionKey`;
- find bucket labels from `dimensionValue`, nested `Dimensions[dimensionKey].ValueStr`, nested `Dimensions[dimensionKey].Value`, or a single nested `Dimensions` value;
- sum raw `metricValue`;
- recalculate bucket percentages from summed raw metric values;
- never average upstream `metricPercentage`;
- track `missingCrowdMaterialIds`.

- [ ] **Step 4: Run aggregation tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_commercial_report -v`

Expected: PASS for aggregation tests.

### Task 2: 可追溯商业报告 CLI

**Files:**
- Modify: `tests/test_commercial_report.py`
- Modify: `scripts/commercial_report_lib.py`
- Modify: `scripts/generate_commercial_report.py`

- [ ] **Step 1: Write failing report generation tests**

Extend `tests/test_commercial_report.py` to create temporary:

- `top_materials.json` with two materials;
- `diagnosis-records/<materialId>.json` with `crowd_analysis` records;
- `peak-output/<materialId>/summary.json` with selected frame metadata and a fake JPEG file.

Assert `generate_report(...)` writes:

- `analysis.json` containing top materials, audience dimensions, high-light summaries, and warnings;
- `report.html` containing a title, core metric cards, audience sections, material cards, method limitation note, and escaped material names.

- [ ] **Step 2: Run report tests and verify RED**

Run: `.venv/bin/python -m unittest tests.test_commercial_report -v`

Expected: FAIL because `generate_report` and CLI wrapper do not exist.

- [ ] **Step 3: Implement report library and CLI**

Add to `scripts/commercial_report_lib.py`:

```python
def generate_report(top_materials_json, peak_output_dir, diagnosis_records_dir, output_json, output_html):
    ...
```

The implementation must load selected materials from `top_materials.json`, load per-material diagnosis records from `<materialId>.json`, read available `summary.json` and frame paths from the high-light output directory, build `analysis.json`, and render a self-contained static HTML report with inline CSS. Missing records or summaries should become warnings instead of failing the whole report unless no materials can be loaded.

Add `scripts/generate_commercial_report.py` CLI:

```bash
python scripts/generate_commercial_report.py \
  --top-materials-json /path/top_materials.json \
  --peak-output-dir /path/frames \
  --diagnosis-records-dir /path/diagnosis-records \
  --output-json /path/analysis.json \
  --output-html /path/report.html
```

- [ ] **Step 4: Run report tests and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_commercial_report -v`

Expected: PASS.

### Task 3: RPA 原始诊断记录持久化

**Files:**
- Modify: `tests/test_peak_frames.py`
- Modify: `scripts/extract_peak_frames.py`

- [ ] **Step 1: Write failing CLI persistence test**

Extend `CliTests.test_rpa_cli_processes_top_materials_json` or add a focused test that passes `--diagnosis-records-dir <dir>` with batch RPA. Assert:

```python
self.assertTrue((records_dir / "764.json").exists())
self.assertEqual(
    json.loads((records_dir / "764.json").read_text(encoding="utf-8")),
    records_for("764"),
)
```

- [ ] **Step 2: Run focused CLI test and verify RED**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.CliTests.test_rpa_cli_processes_top_materials_json -v`

Expected: FAIL because `--diagnosis-records-dir` is not recognized or files are not written.

- [ ] **Step 3: Implement CLI persistence**

Modify `scripts/extract_peak_frames.py`:

- add parser argument `--diagnosis-records-dir`;
- after RPA records are fetched and before video processing, atomically write `records` to `<diagnosis-records-dir>/<materialId>.json`;
- use UTF-8 JSON with `ensure_ascii=False` and `indent=2`;
- keep persistence optional and only active for `--source rpa`.

- [ ] **Step 4: Run focused CLI test and verify GREEN**

Run: `.venv/bin/python -m unittest tests.test_peak_frames.CliTests.test_rpa_cli_processes_top_materials_json -v`

Expected: PASS.

### Task 4: Full verification and simulated report

**Files:**
- No source changes expected after this task unless verification finds a defect.

- [ ] **Step 1: Run all tests**

Run:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m py_compile scripts/*.py
```

Expected: PASS.

- [ ] **Step 2: Run simulated complete report flow**

Create a temporary `tmp/commercial-report-simulated/` fixture with mock TOP materials, mock diagnosis records including `crowd_analysis`, and mock high-light summaries/JPEGs. Run:

```bash
.venv/bin/python scripts/generate_commercial_report.py \
  --top-materials-json tmp/commercial-report-simulated/top_materials.json \
  --peak-output-dir tmp/commercial-report-simulated/peak-output \
  --diagnosis-records-dir tmp/commercial-report-simulated/diagnosis-records \
  --output-json tmp/commercial-report-simulated/analysis.json \
  --output-html tmp/commercial-report-simulated/report.html
```

Expected: command exits `0`, `analysis.json` contains recalculated audience percentages, and `report.html` can be opened as a static HTML report.

- [ ] **Step 3: Commit and push**

Stage only source, docs, and tests; do not stage `tmp/`. Commit with Chinese message:

```bash
git add docs/superpowers/plans/2026-07-20-qianchuan-commercial-report.md scripts tests
git commit -m "功能：生成素材人群聚合商业报告"
git push origin feature/qianchuan-top50-rpa-integration
```

Expected: commit and push succeed.

---

## Self-review

- Spec coverage: covers `crowd_analysis` exposure aggregation, `analysis.json`, commercial report, and method limitation note from the batch commercial analysis design.
- Placeholder scan: no `TBD` or incomplete task remains; each task has explicit files, commands, and expected outcomes.
- Type consistency: function names are stable across tests and implementation: `aggregate_crowd_analysis` and `generate_report`.
