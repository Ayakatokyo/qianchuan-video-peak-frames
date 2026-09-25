"""Failure checkpoints must remain part of the exclusive workflow transaction."""

from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import standard_workflow as workflow
import extract_peak_frames as pipeline
from rpa_batch_runner import BatchInterrupted
from batch_runtime import write_json_atomically
from workflow_status import standard_run_status


def _request():
    return workflow.WorkflowRequest("2026-07-19", "2026-07-25", 2, "123", "shop")


def _fail(*_args, **_kwargs):
    raise RuntimeError("injected stage failure")


@pytest.mark.parametrize("command", ["prepare", "resume", "fetch", "finalize"])
def test_stage_failure_is_written_before_releasing_workflow_ownership(tmp_path, monkeypatch, command):
    request = _request()
    paths = workflow.workflow_paths(tmp_path)
    if command in {"resume", "finalize"}:
        status = "interrupted" if command == "resume" else "analysis_pending"
        write_json_atomically(paths["summary"], workflow._summary(request, paths, status=status))
    if command == "resume":
        write_json_atomically(paths["top_materials"], {"materials": []})
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}", encoding="utf-8")
    original = workflow._write_failure
    checkpoints = []

    def check_exclusive_failure(*args, **kwargs):
        # A competing owner must still be excluded while the old owner writes
        # its terminal status; otherwise it can overwrite the new owner's run.
        checkpoints.append(standard_run_status(tmp_path)["lockState"])
        with pytest.raises(workflow.WorkflowLockError):
            with workflow.acquire_workflow_lock(request, tmp_path):
                pass
        return original(*args, **kwargs)

    monkeypatch.setattr(workflow, "_write_failure", check_exclusive_failure)
    with pytest.raises(workflow.StandardWorkflowError, match="injected stage failure"):
        if command == "prepare":
            workflow.prepare_standard_run(request, tmp_path, export_main=_fail)
        elif command == "resume":
            workflow.resume_standard_run(request, tmp_path, repair_batch=_fail)
        elif command == "fetch":
            workflow.fetch_report_metadata(request, tmp_path, fetch_metadata=_fail)
        else:
            workflow.finalize_standard_run(tmp_path, candidate, render_report=_fail)
    assert checkpoints == ["active"]
    assert standard_run_status(tmp_path)["lockState"] == "missing"


@pytest.mark.parametrize("command", ["prepare", "resume", "fetch", "finalize"])
def test_prelock_validation_failure_preserves_existing_summary(tmp_path, command):
    request = _request()
    paths = workflow.workflow_paths(tmp_path)
    write_json_atomically(paths["summary"], workflow._summary(request, paths, status="analysis_pending"))
    original_bytes = paths["summary"].read_bytes()
    invalid_request = replace(request, start_date="not-a-date")
    with pytest.raises(workflow.StandardWorkflowError):
        if command == "prepare":
            workflow.prepare_standard_run(invalid_request, tmp_path, export_main=_fail)
        elif command == "resume":
            workflow.resume_standard_run(invalid_request, tmp_path, repair_batch=_fail)
        elif command == "fetch":
            workflow.fetch_report_metadata(invalid_request, tmp_path, fetch_metadata=_fail)
        else:
            workflow.finalize_standard_run(tmp_path, tmp_path / "missing-candidate.json", render_report=_fail)
    assert paths["summary"].read_bytes() == original_bytes


def test_batch_interruption_has_a_distinct_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "_resolve_shop_id", lambda _args: "offline-shop")
    monkeypatch.setattr(pipeline, "load_dynamic_params", lambda _args: {})
    monkeypatch.setattr(pipeline, "_load_top_materials_payload", lambda *_args: (
        {"materials": [{"materialId": "m-1"}]}, ["m-1"],
    ))

    def interrupt(*_args, **_kwargs):
        raise BatchInterrupted({"interruption": {"reason": "signal"}})

    monkeypatch.setattr(pipeline, "_run_wave_rpa_batch", interrupt)
    status = pipeline.main([
        "--source", "rpa", "--top-materials-json", str(tmp_path / "top_materials.json"),
        "--target-top-n", "1", "--output-dir", str(tmp_path / "peak-frames"),
    ])
    assert status == 130


@pytest.mark.parametrize("exit_code, expected_status, next_action", [
    (130, "interrupted", "resume"),
    (5, "failed", "inspect"),
])
def test_standard_prepare_distinguishes_signal_from_partial_failure(
    tmp_path, exit_code, expected_status, next_action,
):
    with pytest.raises(workflow.StandardWorkflowError):
        workflow.prepare_standard_run(
            _request(), tmp_path, export_main=lambda _args: 0,
            extract_main=lambda _args: exit_code,
        )
    state = standard_run_status(tmp_path)
    assert state["workflowStatus"] == expected_status
    assert state["nextAction"] == next_action
