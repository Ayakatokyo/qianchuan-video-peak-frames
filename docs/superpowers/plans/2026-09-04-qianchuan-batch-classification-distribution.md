# 本批次分类粒度与分布图优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让本批次高光帧分类在证据充分时细分为更多互斥类别，并在报告中展示按成功高光帧张数统计的分类分布柱状图。

**Architecture:** AI 只负责基于 JPEG 生成分组和分析文案；Presentation 从成功高光帧 registry 确定性投影 `batchClassification.distribution`；HTML 报告在本批次分类外层 Tab 内复用现有 `renderColumnChart()`，并保留旧版无 `distribution` 数据时的前端兼容计算。标准分类和现有批次分类详情不改结构。

**Tech Stack:** Python 3、unittest/pytest、静态 HTML、原生 JavaScript、现有报告 CSS。

**Spec:** `docs/superpowers/specs/2026-09-04-qianchuan-batch-classification-distribution-design.md`

## Global Constraints

- 统计口径固定为成功高光帧张数，不统计素材数。
- AI 不得输出或修正 `frameCount`、`percentage`、`coverage` 等统计数字。
- 分类只能依据成功高光帧 JPEG 中可观察的主要内容/画面机制。
- 同一帧只能归入一个主导机制类别；未可靠归类时进入 `unclassifiedFrameRefs`。
- 不改变标准分类、本批次分类详情、帧分页、灯箱、素材跳转和外层 Tab 状态隔离。
- 不引入新的图表库，不使用浏览器进行验证。
- 每次代码任务必须先写一个会失败的测试，再写最小实现并运行回归测试。

---

### Task 1: 收紧本批次分类提示词的目标粒度

**Files:**
- Modify: `prompts/diagnostic-analysis-prompt-v1.md:98-100,139-177`
- Test: `tests/test_final_report.py`（现有提示词契约测试附近）

**Interfaces:**
- Consumes: Chapter 04 的成功高光帧 JPEG 规则和现有 `customClassification` 输出契约。
- Produces: AI 可执行的按成功帧规模选择分类数量的文本规则，保留现有 JSON 字段和统计数字禁写约束。

- [ ] **Step 1: 写失败测试**

在现有提示词契约测试中增加断言：

```python
prompt_text = (ROOT / "prompts" / "diagnostic-analysis-prompt-v1.md").read_text(encoding="utf-8")
self.assertIn("不少于 12 张", prompt_text)
self.assertIn("5～8 个类别", prompt_text)
self.assertIn("6～11 张", prompt_text)
self.assertIn("4～6 个类别", prompt_text)
self.assertIn("少于 6 张", prompt_text)
self.assertIn("主导机制", prompt_text)
self.assertIn("不得输出或修正帧数、占比、覆盖率", prompt_text)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_final_report.py -k prompt -q`

Expected: FAIL，因为当前提示词没有按帧数区间规定 5～8、4～6 和至少 2 个类别的目标粒度。

- [ ] **Step 3: 写最小提示词修改**

在 Chapter 04 `customClassification` 规则前增加以下明确要求，并在 JSON 输出约束后补充主导机制和统计数字约束：

```text
为提高本批次分类的细致程度，按成功高光帧总量选择目标粒度：不少于 12 张时优先拆分为 5～8 个类别；6～11 张时优先拆分为 4～6 个类别；少于 6 张时至少拆分为 2 个类别。类别必须由 JPEG 中可观察的主要内容/画面机制支撑，不能仅按颜色、远近、画质或偶然姿态拆分同一机制，也不能为了凑数量制造差异。可适用的机制方向包括人物表达/示范、产品或包装证据、开箱礼赠、使用过程、品质或成分证明、生活场景承接、利益点视觉化、步骤或对比展示，但不要求每个批次都出现全部方向。一个画面存在多个信号时，只归入最主要的主导机制。
```

保留“至少 2 个互斥业务语义分组”的兼容性要求，并明确“AI 不得输出或修正帧数、占比、覆盖率等统计数字”。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_final_report.py -k prompt -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add prompts/diagnostic-analysis-prompt-v1.md tests/test_final_report.py
git commit -m "feat: refine batch classification prompt granularity"
```

### Task 2: 在 Presentation 投影确定性生成分类分布

**Files:**
- Modify: `scripts/qianchuan_presentation.py:367-447,606-625`
- Test: `tests/test_qianchuan_presentation.py:94-155`

**Interfaces:**
- Consumes: `_chapter04()` 已过滤的 `projected_groups`、有效 `unclassifiedFrameRefs` 和成功帧总数。
- Produces: `chapter04.batchClassification.distribution: list[dict]`，每项包含 `groupId`、`groupName`、`frameCount`、`percentage`，可选最后一项 `unclassified`。

- [ ] **Step 1: 写失败测试**

在 `PresentationTests` 中扩展有效批次 fixture 的断言：

```python
distribution = chapter04["batchClassification"]["distribution"]
self.assertEqual(
    distribution,
    [
        {"groupId": "person-led", "groupName": "人物场景承接", "frameCount": 1, "percentage": 50.0},
        {"groupId": "product-led", "groupName": "产品证据展示", "frameCount": 1, "percentage": 50.0},
    ],
)
self.assertNotIn("frameRefs", json.dumps(distribution, ensure_ascii=False))
self.assertNotIn("framePath", json.dumps(distribution, ensure_ascii=False))
```

增加未归类测试：将第二帧从第二组移到 `unclassifiedFrameRefs`，断言 `distribution[-1]` 为 `{"groupId": "unclassified", "groupName": "未归类", "frameCount": 1, "percentage": 50.0}`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_qianchuan_presentation.py -k "batch_classification or batch_mode" -q`

Expected: FAIL，因为 `batchClassification` 当前没有 `distribution`。

- [ ] **Step 3: 写最小 Presentation 实现**

在 `_chapter04()` 完成 `projected_groups` 和 `unclassified_refs` 后生成：

```python
distribution = [
    {
        "groupId": group["groupId"],
        "groupName": group["groupName"],
        "frameCount": group["frameCount"],
        "percentage": group["percentage"],
    }
    for group in projected_groups
    if group["frameCount"] > 0
]
if unclassified_refs:
    distribution.append({
        "groupId": "unclassified",
        "groupName": "未归类",
        "frameCount": len(unclassified_refs),
        "percentage": round(len(unclassified_refs) / total_frame_count * 100, 2) if total_frame_count else 0,
    })
```

将 `"distribution": distribution` 放入 `batch_classification`。在 `validate_presentation_report()` 的批次校验中要求 `distribution` 为列表，校验 `groupId` 唯一，并将每个分布项与对应组的 `frameCount`/`percentage` 对比；`unclassified` 项对比 `unclassifiedFrameRefs` 的数量和计算百分比。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_qianchuan_presentation.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add scripts/qianchuan_presentation.py tests/test_qianchuan_presentation.py
git commit -m "feat: project batch classification distribution"
```

### Task 3: 在本批次分类面板渲染分布柱状图

**Files:**
- Modify: `templates/final-report-template.html:2224-2370`
- Modify only if needed: `assets/report/unified-report.css:1704-1775`
- Test: `tests/test_final_report.py:333-370`

**Interfaces:**
- Consumes: `batchClassification.distribution`；旧数据没有该字段时使用 `groups[*].frameRefs`、`frameCount` 或 `factsByReference` 计算兼容分布。
- Produces: `renderBatchClassificationPanel()` 在规则说明和本批次内部 Tab 之间渲染名为“本批次分类分布”的现有列柱图。

- [ ] **Step 1: 写失败测试**

在前端模板契约测试中增加：

```python
self.assertIn("function batchClassificationDistribution", template)
self.assertIn('renderColumnChart("本批次分类分布"', template)
self.assertIn('axisLabel: "帧数"', template)
self.assertIn("bucket.frameCount", template)
self.assertIn("bucket.percentage", template)
self.assertLess(
    template.index('renderColumnChart("本批次分类分布"'),
    template.index('element("div", "highlight-dashboard-tabs highlight-batch-tabs")'),
)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_final_report.py -k "batch_classification_reuses or batch_rule_description" -q`

Expected: FAIL，因为当前模板没有批次分类分布 helper 或柱状图调用。

- [ ] **Step 3: 写最小前端实现**

在 `renderBatchClassificationPanel()` 前增加兼容 helper：优先读取 `batchClassification.distribution`；缺失时将每个非空 group 映射为 `{groupId, groupName, frameCount, percentage, bucket: groupName}`，统计总数后补齐百分比；若存在未归类引用则追加“未归类”。

在确认 `groups.length > 0` 后、创建 `batchTabList` 前插入：

```javascript
const distribution = batchClassificationDistribution(batchClassification, factsByReference);
if (distribution.length > 0) {
  root.append(renderColumnChart("本批次分类分布", distribution, null, {
    cardClass: "batch-classification-distribution",
    axisLabel: "帧数",
    valueAccessor: (bucket) => Number(bucket.frameCount || 0),
    axisValueFormatter: (value) => `${formatNumber(value)} 帧`,
    tooltipLines: (bucket) => [
      `帧数 ${formatNumber(bucket.frameCount)}`,
      `占比 ${formatNumber(bucket.percentage)}%`,
    ],
    itemTitleFormatter: (bucket) => `${text(bucket.groupName)}：${formatNumber(bucket.frameCount)} 帧 · ${formatNumber(bucket.percentage)}%`,
  }));
}
```

不要添加柱点击下钻；沿用现有 `renderColumnChart()` 的文字标签、Tooltip、键盘语义和响应式密度。若没有分布项则不追加图表，保留既有空状态。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_final_report.py -q`

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add templates/final-report-template.html assets/report/unified-report.css tests/test_final_report.py
git commit -m "feat: show batch classification distribution chart"
```

### Task 4: 完成静态检查、回归验证和运行报告数据检查

**Files:**
- Verify: `prompts/diagnostic-analysis-prompt-v1.md`
- Verify: `scripts/qianchuan_presentation.py`
- Verify: `templates/final-report-template.html`
- Verify: `assets/report/unified-report.css`

**Interfaces:**
- Consumes: Tasks 1–3 的提示词、Presentation 投影和前端渲染实现。
- Produces: 可通过相关测试、语法检查和 diff 检查的完整变更；不修改历史运行目录。

- [ ] **Step 1: 运行相关测试**

Run: `python -m pytest tests/test_qianchuan_presentation.py tests/test_final_report.py -q`

Expected: 所有相关测试通过。

- [ ] **Step 2: 运行 Python 和 JavaScript 静态检查**

Run: `python -m compileall -q scripts`

Run: `node --check assets/report/unified-report.js`

对模板内联脚本按仓库既有方式执行语法检查；不能使用浏览器。

- [ ] **Step 3: 检查差异和仓库状态**

Run: `git diff --check`

Run: `git status --short`

Expected: 无空白错误；只包含本次功能相关文件或已确认的本地变更。

- [ ] **Step 4: 运行全量测试并记录既有失败**

Run: `python -m pytest -q`

Expected: 记录全量结果；若仍出现既有 `tests/test_skill_contract.py` 缺少 `docs/skill-flow.md` 的失败，明确标记为基线问题，不修改无关文档。

- [ ] **Step 5: Commit verification checkpoint**

```bash
git add prompts/diagnostic-analysis-prompt-v1.md scripts/qianchuan_presentation.py templates/final-report-template.html tests/test_qianchuan_presentation.py tests/test_final_report.py
git commit -m "test: verify batch classification distribution"
```

若 `git status --short` 显示这些文件没有未提交内容，则跳过该提交；不得使用 `git add -u` 触碰无关的用户变更。
