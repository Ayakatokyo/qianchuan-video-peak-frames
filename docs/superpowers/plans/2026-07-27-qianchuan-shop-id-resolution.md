# Qianchuan Shop ID Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the RPA CLI auto-resolve a single authorized `shop_id` when omitted and expose a read-only account listing entrypoint.

**Architecture:** Keep account lookup in `rpa_adapter.py` and add CLI-only orchestration in `extract_peak_frames.py`. Use TDD to replace the current "shop-id required" contract with single-account auto-resolution while keeping explicit multi-account failures.

**Tech Stack:** Python 3.9+, `unittest`, existing `requests`-backed adapter, argparse CLI.

---

### Task 1: Add CLI contract tests for `shop_id` resolution

**Files:**
- Modify: `tests/test_peak_frames.py`

- [ ] Add a failing test for auto-resolving a single authorized account when `--shop-id` is omitted.
- [ ] Run the focused test and confirm it fails because the CLI still requires `--shop-id`.
- [ ] Add a failing test for rejecting multiple authorized accounts with a clear error.
- [ ] Add a failing test for `--list-shop-accounts` printing account rows without needing `--output-dir`.

### Task 2: Implement CLI resolution and account listing

**Files:**
- Modify: `scripts/extract_peak_frames.py`

- [ ] Import `list_accounts` from `rpa_adapter`.
- [ ] Make `--output-dir` optional at parse time and enforce it only on execution branches that need artifacts.
- [ ] Add helpers to normalize account rows, print them, and resolve the effective `shop_id`.
- [ ] Wire `--list-shop-accounts` to print accounts and exit before any extraction work.
- [ ] Update the RPA branch to resolve `shop_id` automatically when omitted.

### Task 3: Verify focused and regression tests

**Files:**
- Verify: `tests/test_peak_frames.py`

- [ ] Run the new focused CLI tests and confirm they pass.
- [ ] Run the existing RPA CLI test subset to confirm no regression around explicit `--shop-id`.
