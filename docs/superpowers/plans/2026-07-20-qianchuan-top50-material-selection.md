# TOP 50 Material Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only CLI that fetches daily Qianchuan asset-table rows, aggregates eligible materials by overall cost, and atomically emits `top_materials.json`.

**Architecture:** `asset_table_client.py` owns discovery and dynamic-SQL HTTP calls; `top_materials_lib.py` owns pure parsing, filtering, aggregation, ranking, and rendering; `export_top_materials.py` validates arguments and atomically publishes output.

**Tech Stack:** Python 3.9+, `argparse`, `datetime`, `decimal`, `json`, `unittest`, `requests`.

---

### Task 1: Add the read-only asset-table client

**Files:**
- Create: `scripts/asset_table_client.py`
- Create: `tests/test_top_materials.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Write failing metadata and pagination tests.** Add a fake HTTP session whose asset search returns the exact table and whose fields include `dt` as a partition key plus `origin_data`. Assert `discover_table(TABLE)` returns the physical table. Add a two-response SQL test with 2,000 rows then one row; assert `iter_origin_data` returns 2,001 values and the second request SQL contains `LIMIT 2000 OFFSET 2000`.

- [ ] **Step 2: Run `python -m unittest tests.test_top_materials.AssetTableClientTests -v`.** Expected: import failure because `asset_table_client` does not exist.

- [ ] **Step 3: Implement the minimum client.** Define `AssetTableError` and `AssetTableClient(base_url, session_token, session=None, timeout=15, retries=2)`. `discover_table` calls the search, detail, and fields endpoints; requires an exact table-name match, `dt` partition key, and `origin_data`. `iter_origin_data` accepts a metadata-derived identifier only if it matches `[A-Za-z_][A-Za-z0-9_]*`, then loops over this fixed SQL form:

```python
sql = (
    f"SELECT origin_data FROM {physical_table} "
    f"WHERE dt = '{partition_date}' ORDER BY origin_data "
    f"LIMIT 2000 OFFSET {offset}"
)
```

It validates HTTP and platform success, yields only `origs[0]`, stops after a short page, and raises `AssetTableError` for malformed data. No caller can submit SQL.

- [ ] **Step 4: Append `requests>=2.31,<3` to `requirements.txt` and rerun the focused test.** Expected: pass with no network access.

- [ ] **Step 5: Commit with `git add requirements.txt scripts/asset_table_client.py tests/test_top_materials.py && git commit -m "feat: add read-only asset table client"`.**

### Task 2: Implement deterministic local material selection

**Files:**
- Create: `scripts/top_materials_lib.py`
- Modify: `tests/test_top_materials.py`

- [ ] **Step 1: Write failing selection tests.** Add rows in `origin_data` form where material `1` has costs `5` and `4.50` and material `2` has `9.50`; assert ordering is `1`, then `2` by ID tie-break. Add `-2`, matching and nonmatching name/type rows; assert only the match remains and `-2` has one excluded row and `Decimal("7")`. Assert conflicting names for one ID are still aggregated by material ID and choose the dominant metadata variant; assert `NaN` cost raises `MaterialDataError`.

- [ ] **Step 2: Run `python -m unittest tests.test_top_materials.MaterialSelectionTests -v`.** Expected: import failure because `top_materials_lib` does not exist.

- [ ] **Step 3: Implement the pure selection module.** Define `Material`, `ExcludedMaterial`, `MaterialDataError`, `select_materials`, and `build_output`. Read exactly the approved five nested paths, trim string metadata, require finite non-negative `Decimal` cost, apply optional compiled name regex and exact material types before grouping, aggregate all rows by ID, aggregate `-2` separately with its fixed reason, choose conflicting metadata from the highest-cost variant with lexical tie-breaks, and sort by `(-total_cost, material_id)`. `build_output` returns the approved root fields and all repeated item fields, with JSON-number costs.

- [ ] **Step 4: Run `python -m unittest tests.test_top_materials.MaterialSelectionTests -v` and `python -m unittest tests/test_peak_frames.py -v`.** Expected: both pass.

- [ ] **Step 5: Commit with `git add scripts/top_materials_lib.py tests/test_top_materials.py && git commit -m "feat: select top materials by overall cost"`.**

### Task 3: Add the CLI and atomic publication

**Files:**
- Create: `scripts/export_top_materials.py`
- Modify: `tests/test_top_materials.py`

- [ ] **Step 1: Write failing CLI tests.** Patch `export_top_materials.AssetTableClient` with a fake that returns two dates of raw origin rows, run `main` with a temporary output path, and assert exit `0` plus a first material ranked `1`. Patch it with a client that raises on the second partition; assert exit `1` and that no output exists. Repeat with an already existing output and assert no overwrite.

- [ ] **Step 2: Run `python -m unittest tests.test_top_materials.ExportCliTests -v`.** Expected: import failure because `export_top_materials` does not exist.

- [ ] **Step 3: Implement `main(argv=None)`.** Validate ISO dates, compile `--name-regex`, reject non-positive `--top-n`, get the session token from the environment without printing it, discover the fixed table, loop inclusively over dates, select materials, and reject an existing target. Write `json.dumps(payload, ensure_ascii=False, indent=2) + "\n"` into a named temporary file in the output parent, then call `os.replace` only after all requests and processing succeed. Return `0` on success; catch expected client/data/input errors, emit credential-free stderr text, and return `1`.

- [ ] **Step 4: Run `python -m unittest tests/test_top_materials.py -v`, `python -m unittest tests/test_peak_frames.py -v`, and `python -m py_compile scripts/asset_table_client.py scripts/top_materials_lib.py scripts/export_top_materials.py`.** Expected: all return zero.

- [ ] **Step 5: Commit with `git add scripts/export_top_materials.py tests/test_top_materials.py && git commit -m "feat: export top materials json"`.**
