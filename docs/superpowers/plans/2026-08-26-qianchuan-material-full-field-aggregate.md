# 千川素材汇总全字段查询 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让当前技能单次返回日期范围内的素材汇总数据，包含 9 个非时间维度和 102 个已验证指标，并拒绝不完整响应。

**Architecture:** 在 `qianchuan_report_client.py` 维护静态字段契约，不读取动态 config。完成下载和结构化后校验每个记录的 dimensions/metrics 键；通过校验才返回记录并落盘不含凭证的字段清单。既有筛选、排序和 records 输出结构保持不变。

**Tech Stack:** Python 3、`unittest`、`requests`

---

### Task 1: 全字段静态查询契约

**Files:**
- Modify: `scripts/qianchuan_report_client.py:20-119`
- Modify: `tests/test_qianchuan_report_client.py:16-30,85-111`

- [ ] **Step 1: 写入会失败的字段契约测试**

将 `material_record` 改为由客户端字段契约生成完整测试记录：

```python
def material_record(material_id="m-1"):
    dimensions = {field: {"Value": field, "ValueStr": field} for field in client.DEFAULT_DIMENSIONS}
    dimensions["material_id"] = {"Value": material_id, "ValueStr": material_id}
    return {
        "dimensions": dimensions,
        "metrics": {field: {"Value": 1, "ValueStr": "1"} for field in client.DEFAULT_METRICS},
    }
```

在默认查询测试中断言 9 个维度、102 个无重复指标；断言包含 4 个 overall 指标和 `live_show_count_for_roi2_v2`、`video_play_finish_rate_for_roi2_v2`；断言不包含 `roi2_material_advice`、`stat_time_day`、`range_stat_time_hour`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m unittest tests.test_qianchuan_report_client.QianchuanReportClientTests.test_default_query_matches_reference_material_report_contract -v`

Expected: FAIL，当前默认维度为 5 个、指标为 86 个。

- [ ] **Step 3: 最小实现完整字段常量**

将 `DEFAULT_DIMENSIONS` 替换为：

```python
[
    "ecp_app_id", "roi2_material_type_v3", "roi2_material_video_play_info",
    "roi2_material_video_name", "roi2_material_video_type",
    "roi2_material_advice_reason", "material_create_time_v2", "material_id",
    "anchor_id",
]
```

在现有 86 个 `DEFAULT_METRICS` 后追加以下 16 个字段：

```python
"live_show_count_for_roi2_v2", "live_cvr_rate_for_roi2_v2", "live_watch_count_for_roi2_v2", "live_convert_rate_for_roi2_v2", "total_cpc_for_roi2", "total_ecpm_for_roi2", "video_avg_watch_duration_for_roi2", "video_comment_count_for_roi2_v2", "video_follow_count_for_roi2", "video_like_count_for_roi2", "video_play_count_for_roi2_v2", "video_play_duration_2s_rate_for_roi2", "video_play_duration_3s_rate_for_roi2", "video_play_duration_5s_rate_for_roi2", "video_play_duration_10s_rate_for_roi2", "video_play_finish_rate_for_roi2_v2",
```

- [ ] **Step 4: 运行测试确认通过并提交**

Run: `python -m unittest tests.test_qianchuan_report_client.QianchuanReportClientTests.test_default_query_matches_reference_material_report_contract -v`

Expected: PASS。

Run: `git add scripts/qianchuan_report_client.py tests/test_qianchuan_report_client.py && git commit -m "feat: request complete material aggregate fields"`

### Task 2: 完整性校验与可审计字段清单

**Files:**
- Modify: `scripts/qianchuan_report_client.py:188-225,417-422`
- Modify: `tests/test_qianchuan_report_client.py:113-183`

- [ ] **Step 1: 写入会失败的响应完整性测试**

添加成功路径测试：用 `material_record()` 的 payload 调用 `fetch_material_report`，读取 `field-manifest.json`，并断言：

```python
self.assertEqual(manifest["dimensions"], client.DEFAULT_DIMENSIONS)
self.assertEqual(manifest["metrics"], client.DEFAULT_METRICS)
self.assertEqual(manifest["recordCount"], 1)
self.assertEqual(manifest["startTime"], "2026-08-01 00:00:00")
self.assertEqual(manifest["endTime"], "2026-08-07 23:59:59")
self.assertNotIn("authorization-token", json.dumps(manifest))
```

添加缺指标测试：从 `material_record()["metrics"]` 删除 `video_play_finish_rate_for_roi2_v2`，断言 `fetch_material_report` 抛出 `ReportClientError`，消息包含 `missing expected fields` 和该字段名且不含 `authorization-token`。

- [ ] **Step 2: 运行两个新测试确认失败**

Run: `python -m unittest tests.test_qianchuan_report_client.QianchuanReportClientTests.test_fetch_validates_full_contract_and_writes_field_manifest tests.test_qianchuan_report_client.QianchuanReportClientTests.test_fetch_rejects_a_nonempty_record_missing_a_contract_field -v`

Expected: FAIL，尚无清单且缺字段记录尚未被拒绝。

- [ ] **Step 3: 实现校验与清单写入**

新增 `_validate_field_contract()`，在 `build_business_params` 的开始调用。它必须拒绝超过 10 个维度、维度或指标重复、包含 `stat_time_day`/`range_stat_time_hour`、以及不在 `DEFAULT_METRICS` 中的排序字段。

新增 `_validate_record_fields(records)`：逐条检查 dimensions 和 metrics 为字典，并按键是否存在计算缺失字段；存在缺失时抛出 `ReportClientError("material report missing expected fields: " + detail)`。

新增 `_write_field_manifest(source_dir, advertiser_id, start_date, end_date, records)`，用 `build_business_params` 写入 `field-manifest.json`。内容必须且只能包含 `dataTopic`、`dimensions`、`metrics`、`filters`、`orderBy`、`startTime`、`endTime`、`recordCount`。

在 `fetch_material_report` 中，非空检查通过后依次调用 `_validate_record_fields(records)`、`_write_field_manifest(...)`，最后返回 records。

- [ ] **Step 4: 运行完整性测试确认通过并提交**

Run: `python -m unittest tests.test_qianchuan_report_client.QianchuanReportClientTests.test_fetch_validates_full_contract_and_writes_field_manifest tests.test_qianchuan_report_client.QianchuanReportClientTests.test_fetch_rejects_a_nonempty_record_missing_a_contract_field -v`

Expected: PASS。

Run: `git add scripts/qianchuan_report_client.py tests/test_qianchuan_report_client.py && git commit -m "feat: validate qianchuan material field completeness"`

### Task 3: 回归验证

**Files:**
- Verify: `scripts/qianchuan_report_client.py`
- Verify: `tests/test_qianchuan_report_client.py`

- [ ] **Step 1: 执行客户端和仓库测试**

Run: `python -m unittest tests.test_qianchuan_report_client -v && python -m unittest discover -s tests -v`

Expected: PASS，覆盖 payload、文件型 CSV、认证脱敏、字段契约、字段清单和缺字段错误。

- [ ] **Step 2: 静态验证字段契约**

Run: `python -c 'from pathlib import Path; import sys; sys.path.insert(0, str(Path("scripts").resolve())); import qianchuan_report_client as c; assert len(c.DEFAULT_DIMENSIONS) == 9; assert len(c.DEFAULT_METRICS) == 102; assert len(c.DEFAULT_METRICS) == len(set(c.DEFAULT_METRICS)); assert not {"roi2_material_advice", "stat_time_day", "range_stat_time_hour"} & set(c.DEFAULT_DIMENSIONS); print("field contract verified")' && git diff --check && git status --short`

Expected: 输出 `field contract verified`，无空白错误；未跟踪的 `qianchuan-field-coverage-analysis-report.md` 保持不纳入提交。

- [ ] **Step 3: 提交仅在回归修复存在时产生的改动**

Run: `git add scripts/qianchuan_report_client.py tests/test_qianchuan_report_client.py && git commit -m "test: cover qianchuan full-field aggregate contract"`

若第 1 步无需修复，不创建空提交。
