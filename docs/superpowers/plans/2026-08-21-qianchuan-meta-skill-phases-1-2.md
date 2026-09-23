# 千川元技能阶段一、二 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone, configuration-driven `qianchuan-meta-skill` that safely queries registered asset fields and extracts frames from multiple configurable RPA interaction signals without changing the existing standard workflow defaults.

**Architecture:** The new meta-skill directory owns configuration schemas, the field registry, query compilation, and its CLI adapter. Existing asset-table, RPA, wave scheduling, FFmpeg, and fact-package modules remain the execution engine. A policy object extends the peak-frame engine and travels through the existing batch CLI; no user-defined SQL, raw field paths, or secrets are persisted.

**Tech Stack:** Python 3.9+, `unittest`, JSON artifacts, existing `requests`, existing FFmpeg integration.

---

### Task 1: Scaffold the Meta-skill and Field Registry

**Files:**

- Create: `qianchuan-meta-skill/SKILL.md`
- Create: `qianchuan-meta-skill/config/asset-field-registry-v1.json`
- Create: `qianchuan-meta-skill/scripts/field_registry.py`
- Create: `qianchuan-meta-skill/tests/test_field_registry.py`

- [ ] **Step 1: Write the failing registry tests**

```python
def test_loads_confirmed_dimension_and_derived_metric():
    registry = load_field_registry(REGISTRY_PATH)
    assert registry.require("material_type").kind == "dimension"
    assert registry.require("net_settlement_roi").kind == "derived_metric"

def test_rejects_unknown_fields_and_invalid_operators():
    registry = load_field_registry(REGISTRY_PATH)
    with self.assertRaisesRegex(FieldRegistryError, "unknown field"):
        registry.require("arbitrary_sql")
    with self.assertRaisesRegex(FieldRegistryError, "does not allow"):
        registry.validate_operator("overall_cost", "regex")
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest qianchuan-meta-skill/tests/test_field_registry.py -v`

Expected: FAIL because the registry module and configuration do not exist.

- [ ] **Step 3: Add the versioned registry and loader**

Define immutable field entries with `id`, `label`, `kind`, `source`, `operators`, `filterStage`, and `sortable`. Include the six confirmed material dimensions and every confirmed mapping in `artifacts/confirmed_field_mapping.json`; derived entries use explicit numerator/denominator formulas, never user text.

```python
class FieldRegistry:
    def require(self, field_id):
        if field_id not in self._entries:
            raise FieldRegistryError("unknown field: %s" % field_id)
        return self._entries[field_id]

    def validate_operator(self, field_id, operator):
        entry = self.require(field_id)
        if operator not in entry.operators:
            raise FieldRegistryError("field %s does not allow %s" % (field_id, operator))

def load_field_registry(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return FieldRegistry.from_payload(payload)
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `python -m unittest qianchuan-meta-skill/tests/test_field_registry.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -f qianchuan-meta-skill/SKILL.md qianchuan-meta-skill/config/asset-field-registry-v1.json qianchuan-meta-skill/scripts/field_registry.py qianchuan-meta-skill/tests/test_field_registry.py
git commit -m "feat: add qianchuan meta skill field registry"
```

### Task 2: Implement the Safe Material Query Compiler

**Files:**

- Create: `qianchuan-meta-skill/scripts/material_query.py`
- Create: `qianchuan-meta-skill/tests/test_material_query.py`
- Modify: `qianchuan-meta-skill/scripts/field_registry.py`

- [ ] **Step 1: Write the failing query tests**

```python
def test_filters_after_period_aggregation_and_sorts_by_derived_roi():
    result = execute_material_query(records, query, registry)
    self.assertEqual([item["materialId"] for item in result.materials], ["m-2", "m-1"])
    self.assertEqual(result.receipt["sort"]["field"], "net_settlement_roi")

def test_rejects_unknown_field_or_divide_by_zero_sort_value():
    with self.assertRaisesRegex(MaterialQueryError, "unknown field"):
        execute_material_query(records, invalid_query, registry)
    self.assertEqual(result.excluded[0]["reason"], "unavailable_sort_value")
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest qianchuan-meta-skill/tests/test_material_query.py -v`

Expected: FAIL because query execution is not implemented.

- [ ] **Step 3: Implement query normalization, aggregation, and receipts**

Implement `parse_query_config()` and `execute_material_query()`. Accept only `eq`, `in`, `contains`, `regex` for registered dimensions and `eq`, `gt`, `gte`, `lt`, `lte`, `between` for registered metrics. Apply dimension filters while parsing source rows, aggregate atomic metrics per material over the requested period, calculate derived fields after aggregation, apply metric predicates, then sort by `(sortValue, materialId)` with the configured direction.

Write a receipt with normalized query, registry version, candidate/pass/excluded counts, per-material sort value, and non-secret exclusion reasons. Return the complete registered atomic-metric aggregates required to write the existing-compatible `asset-material-facts-v1.json`; the meta workflow must derive top-material ROI facts from this artifact rather than recomputing commercial values.

- [ ] **Step 4: Add malformed-config and stable-tie cases**

Cover invalid dates, nonnumeric advertiser IDs, empty `in`, invalid regex, missing derived denominator, ascending and descending stable ties, and metric conditions that encounter unavailable values.

- [ ] **Step 5: Run focused query tests**

Run: `python -m unittest qianchuan-meta-skill/tests/test_material_query.py qianchuan-meta-skill/tests/test_field_registry.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -f qianchuan-meta-skill/scripts/material_query.py qianchuan-meta-skill/scripts/field_registry.py qianchuan-meta-skill/tests/test_material_query.py
git commit -m "feat: add configurable material query compiler"
```

### Task 3: Generalize Peak Selection with Policy Evidence

**Files:**

- Modify: `scripts/peak_frame_lib.py`
- Modify: `tests/test_peak_frames.py`

- [ ] **Step 1: Write the failing peak-policy tests**

```python
def test_selects_multiple_metric_policies_and_merges_same_timestamp():
    selection = select_highlight_frames(records, policy)
    frame = next(item for item in selection.frames if item.duration_sec == 4)
    self.assertEqual({item.metric for item in frame.selection_evidence}, {"CLICK_COUNT", "SHARE_COUNT"})

def test_global_maximum_uses_earliest_timestamp_for_ties():
    selection = select_highlight_frames(records, global_max_policy)
    self.assertEqual(selection.frames[0].duration_sec, 3)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest tests/test_peak_frames.py -v`

Expected: FAIL because selection is fixed to `CLICK_COUNT` local peaks and frames have no policy evidence.

- [ ] **Step 3: Add policy normalization and generic series parsing**

Introduce supported metric constants, `HighlightPolicy`, `MetricPolicy`, and `SelectionEvidence`. Replace the fixed `parse_click_series()` internals with a metric-aware parser while retaining `parse_click_series()` as a compatibility wrapper. Reject unsupported metrics, duplicate policy entries, nonpositive `maxFrames`, and `global_maximum` values other than one.

- [ ] **Step 4: Implement local-peak and global-maximum selection**

Reuse plateau collapse and deterministic sorting for `local_peaks`. For `global_maximum`, inspect every valid series point and choose the maximum value with earliest-time tie breaking. Merge selections by canonical timestamp and retain every policy-evidence item. Treat `includeZeroSecond` as a separate `zero_second` evidence item and retain current default behavior when no policy is supplied.

- [ ] **Step 5: Persist policy evidence in summaries**

Extend `build_summary()` and markdown rendering so each frame carries `selectionEvidence`. Preserve existing `rankType`, `durationSec`, and `metricValue` fields for legacy consumers. A selected but unavailable metric writes an availability reason without failing other policies.

- [ ] **Step 6: Run focused compatibility and edge-case tests**

Run: `python -m unittest tests/test_peak_frames.py -v`

Expected: PASS, including existing fixed-`CLICK_COUNT` tests and new six-metric, plateau, boundary, tie, and deduplication cases.

- [ ] **Step 7: Commit**

```bash
git add scripts/peak_frame_lib.py tests/test_peak_frames.py
git commit -m "feat: support configurable multi-metric highlight frames"
```

### Task 4: Pass Highlight Policies Through the Online Batch Pipeline

**Files:**

- Modify: `scripts/extract_peak_frames.py`
- Modify: `scripts/schemas.py`
- Modify: `scripts/repair_manifest.py`
- Modify: `tests/test_peak_frames.py`
- Modify: `tests/test_schemas.py`
- Modify: `tests/test_repair_manifest.py`

- [ ] **Step 1: Write failing CLI and manifest tests**

```python
def test_rpa_cli_persists_normalized_highlight_policy_in_manifest():
    self.assertEqual(manifest["highlightPolicy"]["policies"][0]["metric"], "LIKE_COUNT")

def test_resume_reuses_manifest_highlight_policy():
    resumed = load_wave_resume(manifest_path, args, candidates, "resume")
    self.assertEqual(resumed["highlightPolicy"], original_policy)
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `python -m unittest tests/test_peak_frames.py tests/test_schemas.py tests/test_repair_manifest.py -v`

Expected: FAIL because the batch CLI and manifest have no highlight-policy field.

- [ ] **Step 3: Add a JSON policy argument and validation**

Add `--highlight-policy-json` to `extract_peak_frames.py` for RPA and local record processing. Read UTF-8 JSON, normalize it through `peak_frame_lib`, and pass the normalized policy to every postprocess attempt. Absence means the current fixed `CLICK_COUNT` default.

- [ ] **Step 4: Freeze policy into run state and repair behavior**

Add `highlightPolicy` to batch-manifest schema version 2 as an optional validated field. On `--resume`, reject a different supplied policy and reuse the original frozen policy. Make `repair_manifest.py` use the manifest policy for reprocessed material summaries.

- [ ] **Step 5: Run focused pipeline suites**

Run: `python -m unittest tests/test_peak_frames.py tests/test_schemas.py tests/test_repair_manifest.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/extract_peak_frames.py scripts/schemas.py scripts/repair_manifest.py tests/test_peak_frames.py tests/test_schemas.py tests/test_repair_manifest.py
git commit -m "feat: preserve highlight policies in batch runs"
```

### Task 5: Add the Meta-skill CLI Adapter and A-E Workflow Reuse

**Files:**

- Create: `qianchuan-meta-skill/scripts/run_meta_skill.py`
- Create: `qianchuan-meta-skill/scripts/meta_workflow.py`
- Create: `qianchuan-meta-skill/tests/test_meta_workflow.py`
- Modify: `qianchuan-meta-skill/SKILL.md`

- [ ] **Step 1: Write the failing end-to-end adapter tests**

```python
def test_prepare_writes_query_receipt_then_delegates_existing_batch_engine():
    summary = prepare_meta_run(config_path, output_dir, dependencies=fakes)
    self.assertEqual(summary["status"], "facts_ready")
    self.assertTrue((output_dir / "query-receipt.json").is_file())

def test_prepare_never_persists_credentials():
    text = "\n".join(path.read_text() for path in output_dir.rglob("*.json"))
    self.assertNotIn("secret-token", text)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `python -m unittest qianchuan-meta-skill/tests/test_meta_workflow.py -v`

Expected: FAIL because the meta workflow does not exist.

- [ ] **Step 3: Implement `validate`, `prepare`, and `status` commands**

`validate` reads and validates the config without network access. `prepare` obtains source rows through the existing `AssetTableClient`, invokes the query compiler, writes `top_materials.json`, `asset-material-facts-v1.json`, and `query-receipt.json`, derives the existing-compatible top-material ROI facts, then delegates RPA batch collection and fact-package generation using the policy file. `status` delegates to retained run artifacts. Do not call AI analysis or final-report rendering.

- [ ] **Step 4: Document usage and explicit lifecycle limits**

Document required environment variables by name only. Define that `prepare` may call the asset table, RPA, video download, and FFmpeg, while `validate` does not. State that stages three and four are not implemented.

- [ ] **Step 5: Run adapter-focused tests**

Run: `python -m unittest qianchuan-meta-skill/tests/test_meta_workflow.py qianchuan-meta-skill/tests/test_material_query.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -f qianchuan-meta-skill/SKILL.md qianchuan-meta-skill/scripts/run_meta_skill.py qianchuan-meta-skill/scripts/meta_workflow.py qianchuan-meta-skill/tests/test_meta_workflow.py
git commit -m "feat: add qianchuan meta skill workflow"
```

### Task 6: Verify Regression and User-facing Contracts

**Files:**

- Modify: `tests/test_skill_contract.py`
- Modify: `references/rpa-output-contract.md`
- Modify: `references/qianchuan-operations-runbook.md`

- [ ] **Step 1: Add regression tests for unchanged standard behavior**

Assert that the original standard workflow still supplies no custom policy and continues to use `CLICK_COUNT` local peaks. Assert the meta-skill documentation does not advertise AI analysis or standalone-skill generation.

- [ ] **Step 2: Run the new tests to verify the intended failures before documentation updates**

Run: `python -m unittest tests/test_skill_contract.py tests/test_standard_workflow.py -v`

Expected: FAIL only for missing meta-skill contract assertions.

- [ ] **Step 3: Update operational contracts**

Document the allowed interaction metric IDs, the two selection modes, frozen-policy resume behavior, `query-receipt.json`, and the fact that only `validate` is network-free. Do not change existing standard-flow instructions.

- [ ] **Step 4: Run the complete test suite**

Run: `python -m unittest discover -s tests -v && python -m unittest discover -s qianchuan-meta-skill/tests -v`

Expected: PASS with zero failures.

- [ ] **Step 5: Inspect the final diff and commit**

```bash
git diff --check
git status --short
git add tests/test_skill_contract.py tests/test_standard_workflow.py references/rpa-output-contract.md references/qianchuan-operations-runbook.md
git commit -m "docs: document qianchuan meta skill contracts"
```
