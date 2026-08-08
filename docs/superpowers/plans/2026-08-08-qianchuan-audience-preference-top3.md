# 人群洞察核心人群偏好 TOP3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `02｜人群洞察` 渲染最终入选素材的八大人群偏好 TOP3，按素材内展现占比确定唯一人群归属。

**Architecture:** `diagnostic_analysis_lib` 从既有保留的 `crowd_analysis` 记录中构造 `audience.preferences`，不调用或改变 RPA。`final_report_lib` 已将完整 `audience` 对象透传给模板；模板在 AI 人群洞察与可折叠真实数据之间渲染默认展开的偏好卡，并复用既有单素材 Tab 跳转。

**Tech Stack:** Python 标准库、`unittest`、现有内嵌 HTML/CSS/JavaScript 报告模板。

---

## File Structure

- Modify: `scripts/diagnostic_analysis_lib.py`：生成确定性 `audience.preferences` 事实。
- Modify: `tests/test_diagnostic_analysis.py`：覆盖素材归属、并列排除和稳定 TOP3 排序。
- Modify: `templates/final-report-template.html`：在第 02 部分增加外层偏好挂载点及其渲染函数。
- Modify: `tests/test_final_report.py`：验证报告数据透传及模板层级、单素材 Tab 跳转和空状态。
- Create: `docs/superpowers/specs/2026-08-08-qianchuan-audience-preference-top3-design.md`：已提交的设计基线，不在实现提交中再次修改。

### Task 1: 构造八大人群偏好事实

**Files:**
- Modify: `tests/test_diagnostic_analysis.py:64`
- Modify: `scripts/diagnostic_analysis_lib.py:220-282, 663-678`

- [ ] **Step 1: 写入失败的素材归属与排序测试**

在 `DiagnosticAnalysisAudienceTests` 新增以下测试。测试输入四条最终素材，其中 `m-1`、`m-2`、`m-3` 都唯一归属“新锐白领”，`m-4` 两个分桶并列而不归属；`m-1` 的同分桶记录相加，验证上游 `metricPercentage` 不参与计算。`m-1` 和 `m-2` 占比相同，按 `totalCost` 排序；输出最多三条。

```python
def test_builds_preference_top3_from_unique_show_count_leaders(self):
    top_payload = {
        "materials": [
            {"rank": 1, "materialId": "m-1", "materialName": "素材一", "totalCost": 100},
            {"rank": 2, "materialId": "m-2", "materialName": "素材二", "totalCost": 200},
            {"rank": 3, "materialId": "m-3", "materialName": "素材三", "totalCost": 50},
            {"rank": 4, "materialId": "m-4", "materialName": "素材四", "totalCost": 999},
        ]
    }
    records_by_material = {
        "m-1": [
            crowd_record("m-1", "SHOW_COUNT", "user_group_label_name", "新锐白领", 40),
            crowd_record("m-1", "SHOW_COUNT", "user_group_label_name", "新锐白领", 20),
            crowd_record("m-1", "SHOW_COUNT", "user_group_label_name", "资深中产", 40),
        ],
        "m-2": [
            crowd_record("m-2", "SHOW_COUNT", "user_group_label_name", "新锐白领", 60),
            crowd_record("m-2", "SHOW_COUNT", "user_group_label_name", "资深中产", 40),
        ],
        "m-3": [
            crowd_record("m-3", "SHOW_COUNT", "user_group_label_name", "新锐白领", 55),
            crowd_record("m-3", "SHOW_COUNT", "user_group_label_name", "资深中产", 45),
        ],
        "m-4": [
            crowd_record("m-4", "SHOW_COUNT", "user_group_label_name", "新锐白领", 50),
            crowd_record("m-4", "SHOW_COUNT", "user_group_label_name", "资深中产", 50),
        ],
    }

    preferences = build_diagnostic_analysis(top_payload, records_by_material)["audience"]["preferences"]

    self.assertEqual(preferences, [{
        "audienceGroup": "新锐白领",
        "topMaterials": [
            {"rank": 1, "materialId": "m-2", "materialName": "素材二", "audiencePercentage": 60.0, "audienceShowCount": 60.0, "totalCost": 200.0},
            {"rank": 2, "materialId": "m-1", "materialName": "素材一", "audiencePercentage": 60.0, "audienceShowCount": 60.0, "totalCost": 100.0},
            {"rank": 3, "materialId": "m-3", "materialName": "素材三", "audiencePercentage": 55.0, "audienceShowCount": 55.0, "totalCost": 50.0},
        ],
    }])
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisAudienceTests.test_builds_preference_top3_from_unique_show_count_leaders -v`

Expected: FAIL，原因是事实包尚无 `audience.preferences`。

- [ ] **Step 3: 以最小实现生成 `audience.preferences`**

在 `scripts/diagnostic_analysis_lib.py` 的常量区新增：

```python
PREFERENCE_METRIC = "SHOW_COUNT"
PREFERENCE_DIMENSION = "user_group_label_name"
PREFERENCE_LIMIT = 3
```

在 `_aggregate_audience_metric()` 之后新增 `_aggregate_audience_preferences(records_by_material, materials)`：

```python
def _aggregate_audience_preferences(records_by_material, materials):
    groups = {}
    for material in materials:
        material_id = material["materialId"]
        buckets = {}
        for record in records_by_material.get(material_id, []):
            if not isinstance(record, dict):
                continue
            if record.get("recordType") != "crowd_analysis":
                continue
            if record.get("displayMetric") != PREFERENCE_METRIC:
                continue
            if _text(record.get("dimensionKey")) != PREFERENCE_DIMENSION:
                continue
            if (record_material_id := _text(record.get("materialId"))) and record_material_id != material_id:
                continue
            bucket = _bucket_label(record, PREFERENCE_DIMENSION)
            value = _crowd_metric_value(record)
            if not bucket or value is None or value < 0:
                continue
            buckets[bucket] = buckets.get(bucket, 0.0) + value
        total = sum(buckets.values())
        if total <= 0:
            continue
        maximum = max(buckets.values())
        leaders = [bucket for bucket, value in buckets.items() if value == maximum]
        if len(leaders) != 1:
            continue
        group = leaders[0]
        groups.setdefault(group, []).append({
            "materialId": material_id,
            "materialName": material["materialName"],
            "audiencePercentage": round(maximum / total * 100.0, 6),
            "audienceShowCount": round(maximum, 6),
            "totalCost": round(_finite_number(material.get("totalCost")) or 0.0, 6),
        })
    return [
        {"audienceGroup": group, "topMaterials": [dict(item, rank=index) for index, item in enumerate(sorted(items, key=lambda item: (-item["audiencePercentage"], -item["totalCost"], item["materialId"]))[:PREFERENCE_LIMIT], start=1)]}
        for group, items in sorted(groups.items())
    ]
```

在 `build_diagnostic_analysis()` 中，将 `materials` 传给该函数，并在 `"audience"` 下添加：

```python
"preferences": _aggregate_audience_preferences(records_by_material, materials),
```

- [ ] **Step 4: 运行事实包测试并确认通过**

Run: `python3 -m unittest tests.test_diagnostic_analysis.DiagnosticAnalysisAudienceTests -v`

Expected: PASS，包含新增归属、并列排除和排名测试。

- [ ] **Step 5: 提交事实包与测试**

```bash
git add scripts/diagnostic_analysis_lib.py tests/test_diagnostic_analysis.py
git commit -m "feat: build audience preference top3 facts"
```

### Task 2: 渲染外层偏好卡片

**Files:**
- Modify: `tests/test_final_report.py:1688-1711, 2516-2552`
- Modify: `templates/final-report-template.html:1199-1205, 1675-1720, 2708-2719`

- [ ] **Step 1: 写入失败的报告数据与模板结构测试**

在最终报告数据测试中，对 `build_final_report_data()` 的结果断言偏好事实完整透传：

```python
self.assertEqual(
    data["audience"]["preferences"][0]["topMaterials"][0]["materialId"],
    "m-1",
)
```

在模板契约测试中断言新挂载点、渲染函数和调用位置：

```python
self.assertIn('id="audience-preferences"', html_text)
self.assertIn("function renderAudiencePreferences", html_text)
self.assertIn("renderAudiencePreferences();", html_text)
self.assertIn(
    'renderInsightList("audience-primary", reportData.batchInsights?.audience, "暂无人群洞察");\n  renderAudiencePreferences();\n  renderAudienceDashboard();',
    html_text,
)
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `python3 -m unittest tests.test_final_report.FinalReportDataTests tests.test_final_report.FinalReportRenderTests -v`

Expected: FAIL，模板尚无 `audience-preferences` 挂载点和渲染函数。

- [ ] **Step 3: 增加默认展开的偏好卡与单素材 Tab 跳转**

在第 02 部分的 `audience-primary` 后插入：

```html
<div class="audience-preferences" id="audience-preferences"></div>
```

在 `renderAudienceDashboard()` 前新增：

```javascript
function renderAudiencePreferences() {
  const root = document.getElementById("audience-preferences");
  const preferences = Array.isArray(reportData.audience?.preferences)
    ? reportData.audience.preferences : [];
  root.replaceChildren();
  const card = element("div", "preference-card");
  card.append(element("h3", "", "核心人群偏好 TOP3 素材"));
  if (preferences.length === 0) {
    card.append(element("p", "muted", "暂无核心人群偏好数据"));
    root.append(card);
    return;
  }
  const groups = element("div", "preference-groups");
  preferences.forEach((preference) => {
    const group = element("section", "preference-group");
    group.append(element("h4", "", text(preference.audienceGroup)));
    const list = element("ol", "preference-list");
    (preference.topMaterials || []).forEach((item) => {
      const row = element("li", "preference-item");
      const link = materialLink(text(item.materialId));
      link.textContent = text(item.materialName, item.materialId);
      row.append(
        element("span", "preference-rank", `TOP ${text(item.rank)}`),
        link,
        element("span", "preference-metric", `人群占比 ${formatNumber(item.audiencePercentage)}%`),
        element("span", "preference-cost", `素材消耗 ${formatNumber(item.totalCost)}`),
      );
      list.append(row);
    });
    group.append(list);
    groups.append(group);
  });
  card.append(groups);
  root.append(card);
}
```

在模板现有卡片样式区添加以下样式；不使用收起按钮，也不把它们嵌入 `.data-dashboard`：

```css
.audience-preferences { display: grid; gap: 12px; }
.preference-card { display: grid; gap: 14px; }
.preference-card h3 { font-size: 18px; margin: 0; }
.preference-groups { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
.preference-group { border-top: 1px solid var(--line); padding-top: 12px; }
.preference-group h4 { font-size: 16px; line-height: 1.45; margin: 0; }
.preference-list { display: grid; gap: 8px; list-style: none; margin: 10px 0 0; padding: 0; }
.preference-item { align-items: baseline; color: var(--muted); display: grid; font-size: 13px; gap: 8px; grid-template-columns: max-content minmax(0, 1fr) max-content max-content; line-height: 1.45; }
.preference-rank { color: #7d4a25; font-weight: 800; white-space: nowrap; }
.preference-item .material-link { min-width: 0; overflow-wrap: anywhere; text-align: left; }
.preference-metric, .preference-cost { white-space: nowrap; }
@media (max-width: 640px) {
  .preference-item { grid-template-columns: max-content minmax(0, 1fr); }
  .preference-metric, .preference-cost { grid-column: 2; white-space: normal; }
}
```

在 `renderReport()` 中维持固定顺序：

```javascript
renderInsightList("audience-primary", reportData.batchInsights?.audience, "暂无人群洞察");
renderAudiencePreferences();
renderAudienceDashboard();
```

- [ ] **Step 4: 运行报告测试并确认通过**

Run: `python3 -m unittest tests.test_final_report -v`

Expected: PASS，现有报告兼容测试与新增页面层级测试均通过。

- [ ] **Step 5: 提交模板与报告测试**

```bash
git add templates/final-report-template.html tests/test_final_report.py
git commit -m "feat: render audience preference top3"
```

### Task 3: 回归、冻结边界和交付验证

**Files:**
- Verify only: `scripts/rpa_adapter.py`
- Verify only: `scripts/rpa_batch_runner.py`
- Verify only: `scripts/rpa_runtime.py`
- Verify only: `scripts/rpa_artifact_receipts.py`
- Verify only: `scripts/rpa_csv_parser.py`

- [ ] **Step 1: 运行完整测试回归**

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

Expected: PASS，所有既有与新增测试通过。

- [ ] **Step 2: 检查 RPA 冻结文件未发生变更**

Run: `git diff -- scripts/rpa_adapter.py scripts/rpa_batch_runner.py scripts/rpa_runtime.py scripts/rpa_artifact_receipts.py scripts/rpa_csv_parser.py`

Expected: 无输出。

- [ ] **Step 3: 检查工作树与实现差异**

Run: `git diff --check && git status --short`

Expected: 无空白错误；仅显示本工作区原有未提交改动及本次尚未提交文件（如有）。

- [ ] **Step 4: 记录验证结果**

在交付说明中记录完整测试结果、RPA 冻结文件差异检查结果，以及偏好卡位于 AI 人群洞察与可折叠真实数据之间的确认结果。
