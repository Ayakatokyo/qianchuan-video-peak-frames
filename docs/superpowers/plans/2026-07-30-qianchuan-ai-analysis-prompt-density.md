# Qianchuan AI Analysis Prompt Density Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI second-pass report prompt generate richer analytical conclusions with less repeated data narration, aligned to the existing five-section HTML report.

**Architecture:** Keep deterministic report generation unchanged. Strengthen the prompt contract and regression test so `diagnostic-ai-analysis.json` remains schema v2 but its prose favors interpretation, synthesis, tradeoffs, and next-step reasoning over raw metric recitation.

**Tech Stack:** Markdown prompt template, Python `unittest`, existing final report tests.

---

### Task 1: Prompt Contract Regression

**Files:**
- Modify: `tests/test_final_report.py`
- Modify: `prompts/diagnostic-analysis-prompt-v1.md`

- [ ] **Step 1: Write the failing test**

Add assertions to `FinalReportRenderTests.test_prompt_explains_material_ai_summary_complements_raw_fact_cards`:

```python
self.assertIn("少量关键事实锚点 + 多句分析判断", prompt_text)
self.assertIn("整体结论的数据陈述只作为判断依据，不作为段落主体", prompt_text)
self.assertIn("优先输出组合公式、机会判断、风险边界和下一轮验证方向", prompt_text)
self.assertIn("每条 batchInsights.conclusion 建议 180-280 个中文字符", prompt_text)
self.assertIn("每条 `materialAnalyses[*].audienceConclusion`、`scriptConclusion` 和 `integratedConclusion` 建议 120-220 个中文字符", prompt_text)
self.assertIn("至少输出 2 条 integrated、3 条 audience、2 条 script 洞察", prompt_text)
self.assertIn("至少输出 4 条 nextActions", prompt_text)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportRenderTests.test_prompt_explains_material_ai_summary_complements_raw_fact_cards -v
```

Expected: FAIL because the new prompt phrases are not present.

- [ ] **Step 3: Update the prompt**

Edit `prompts/diagnostic-analysis-prompt-v1.md`:

- Replace the old data-first short-summary instruction with “少量关键事实锚点 + 多句分析判断”.
- Add a conclusion style rule that says raw data should only support the judgment, not dominate the paragraph.
- Increase content density requirements for `overallConclusion`, `batchInsights`, `materialAnalyses`, and `nextActions`.
- Add minimum counts for integrated, audience, script, and strategy items.
- Keep evidence validation, JPEG viewing, execution-process filtering, and HTML section mapping unchanged.

- [ ] **Step 4: Run focused test to verify it passes**

Run:

```bash
python -m unittest tests.test_final_report.FinalReportRenderTests.test_prompt_explains_material_ai_summary_complements_raw_fact_cards -v
```

Expected: PASS.

- [ ] **Step 5: Run broader final report tests**

Run:

```bash
python -m unittest tests.test_final_report -v
```

Expected: PASS.
