"""A failed first detail batch must never spend the remaining RPA quota."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_rpa_batch_runner as batch_fixtures
import extract_peak_frames as pipeline
import workflow_status


@pytest.mark.parametrize("extra", [[], ["--no-download"]])
def test_direct_dynamic_fetch_cli_cannot_bypass_first_batch_gate(monkeypatch, capsys, extra):
    import sys
    import rpa_dynamic_client as dynamic
    monkeypatch.setattr(sys, "argv", ["rpa_dynamic_client.py", "fetch", "--shop-id", "shop", *extra])
    monkeypatch.setattr(dynamic, "fetch_connector_detail",
                        lambda: pytest.fail("direct fetch reached the network"))
    assert dynamic.main() != 0
    assert "run_standard_analysis.py" in capsys.readouterr().err


def failure(entry, status="final_failed"):
    return dict(entry, rpaStatus="rejected", rpaResultStatus=status,
                failureReason="rpa_payload_invalid", postprocessStatus="not_started")


@pytest.mark.parametrize("barrier", [True, False])
def test_all_failed_first_batch_stops_before_any_later_candidate(barrier):
    harness = batch_fixtures.WaveBatchRunnerTests()
    harness.setUp()
    submitted, processed = [], []

    def fetch(_args, entry):
        submitted.append(entry["materialId"])
        return failure(entry)

    manifest, failed, result = harness.run_batch(
        ["m-1", "m-2", "m-3", "m-4", "m-5", "m-6"], fetch,
        lambda _args, entry: processed.append(entry["materialId"]),
        args={"target_top_n": 4, "rpa_concurrency": 3, "rpa_wave_barrier": barrier})
    assert set(submitted) == {"m-1", "m-2", "m-3"}
    assert processed == []
    assert failed and result is None
    gate = manifest["firstBatchCsvGate"]
    assert gate["status"] == "blocked"
    assert gate["errorCode"] == "first_batch_no_valid_csv"
    assert gate["validCsvCount"] == 0
    assert "可能" in gate["message"]
    assert [item["rpaStatus"] for item in manifest["materials"][3:]] == ["pending"] * 3


def test_unknown_first_result_is_not_relabelled_failure_or_replaced():
    harness = batch_fixtures.WaveBatchRunnerTests()
    harness.setUp()
    submitted = []

    def fetch(_args, entry):
        submitted.append(entry["materialId"])
        return (failure(entry, "pending") if entry["materialId"] == "m-2"
                else harness._verified_rpa_success(entry))

    manifest, failed, _result = harness.run_batch(
        ["m-1", "m-2", "m-3"], fetch, harness._success_postprocess,
        args={"target_top_n": 2, "rpa_concurrency": 2})
    assert set(submitted) == {"m-1", "m-2"}
    assert failed
    assert manifest["firstBatchCsvGate"]["status"] == "unconfirmed"
    assert manifest["rejectedMaterials"][0]["rpaResultStatus"] == "pending"


def test_blocked_gate_survives_new_invocation_without_resume(tmp_path):
    harness = batch_fixtures.WaveBatchRunnerTests()
    harness.setUp()
    manifest_path = tmp_path / "batch-manifest.json"
    manifest_path.write_text(json.dumps({"firstBatchCsvGate": {
        "status": "blocked", "materialIds": ["m-1"], "validCsvCount": 0,
        "errorCode": "first_batch_no_valid_csv", "message": "当前入参可能有问题"}}))
    with pytest.raises(RuntimeError, match="可能"):
        import rpa_batch_runner
        rpa_batch_runner.run_wave_batch(
            harness._args(target_top_n=1), {"materials": [{"materialId": "m-1"}]},
            wave_entry=harness._wave_entry,
            resume_state=lambda *_args: pytest.fail("blocked run must not restart"),
            attempt_rpa=lambda *_args: pytest.fail("no further submission"),
            attempt_postprocess=harness._success_postprocess,
            merge_timings=harness._merge_timings, new_manifest=harness._new_manifest,
            write_json=harness._write_json, manifest_path=manifest_path,
            ready_payload=lambda *_args: {}, ready_path=tmp_path / "ready.json",
            entry_result=lambda item: item, elapsed=lambda _started: 0,
            accounted_indices=rpa_batch_runner.accounted_indices,
            ordered=rpa_batch_runner.ordered)


def test_partial_first_batch_keeps_original_backfill_behavior():
    harness = batch_fixtures.WaveBatchRunnerTests()
    harness.setUp()
    submitted = []

    def fetch(_args, entry):
        submitted.append(entry["materialId"])
        return failure(entry) if entry["materialId"] == "m-1" else harness._verified_rpa_success(entry)

    manifest, failed, _result = harness.run_batch(
        ["m-1", "m-2", "m-3", "m-4"], fetch, harness._success_postprocess,
        args={"target_top_n": 3, "rpa_concurrency": 3})
    assert set(submitted) == {"m-1", "m-2", "m-3", "m-4"}
    assert not failed
    assert manifest["firstBatchCsvGate"]["status"] == "passed"


def test_legacy_failed_first_batch_resume_cannot_move_to_new_candidates():
    harness = batch_fixtures.WaveBatchRunnerTests()
    harness.setUp()

    def resume(_args, _candidates, entries):
        rejected = {}
        for index in (0, 1):
            entries[index] = failure(entries[index])
            rejected[index] = entries[index]
        return dict(rejected), {}, rejected, {}, []

    manifest, failed, _result = harness.run_batch(
        ["m-1", "m-2", "m-3"], lambda *_args: pytest.fail("must stop before new RPA"),
        harness._success_postprocess, args={"target_top_n": 2, "rpa_concurrency": 2},
        resume_state=resume)
    assert failed
    assert manifest["firstBatchCsvGate"]["status"] == "blocked"


def test_unknown_resume_only_polls_existing_task_before_later_submission(tmp_path, monkeypatch):
    import test_batch_resume_recovery as recovery
    from rpa_artifact_receipts import publish_receipt
    args, candidates, entries, manifest = recovery.resume_fixture(
        tmp_path, rpaStatus="rejected", rpaResultStatus="unknown", remoteTaskGroupId="saved-task")
    publish_receipt(tmp_path / "rpa-receipts", material_id="m-1",
                    client_attempt_id="saved-attempt", state="submitted", remote_task_group_id="saved-task")
    monkeypatch.setattr(pipeline, "_load_wave_resume_manifest", lambda *_args: manifest)
    _submitted, selected, rejected, _cancelled, _queue = pipeline._resume_wave_state(args, candidates, entries)
    assert not selected and not rejected
    assert entries[0]["remoteTaskGroupId"] == "saved-task"
    assert entries[0]["rpaClientAttemptId"] == "saved-attempt"


def test_standard_resume_blocks_before_repair_or_rpa(tmp_path):
    import standard_workflow as workflow
    request = workflow.WorkflowRequest("2026-07-19", "2026-07-25", 2, "123", "shop")
    paths = workflow.workflow_paths(tmp_path)
    paths["peak_frames"].mkdir(parents=True)
    paths["summary"].write_text(json.dumps(workflow._summary(request, paths, status="failed")))
    (paths["peak_frames"] / "batch-manifest.json").write_text(json.dumps({"firstBatchCsvGate": {
        "status": "blocked", "materialIds": ["m-1", "m-2"], "message": "当前入参可能有问题"}}))
    with pytest.raises(workflow.StandardWorkflowError, match="可能"):
        workflow.resume_standard_run(request, tmp_path,
            repair_batch=lambda *_args, **_kwargs: pytest.fail("must not repair before checking gate"),
            extract_main=lambda *_args: pytest.fail("must not start RPA"))


@pytest.mark.parametrize("state", ["blocked", "pending", "unconfirmed"])
def test_prepare_preserves_existing_gate_and_summary_before_selection(tmp_path, state):
    import standard_workflow as workflow
    request = workflow.WorkflowRequest("2026-07-19", "2026-07-25", 2, "123", "shop")
    paths = workflow.workflow_paths(tmp_path)
    paths["peak_frames"].mkdir(parents=True)
    paths["summary"].write_text(json.dumps(workflow._summary(request, paths, status="paused")))
    summary_before = paths["summary"].read_bytes()
    manifest_path = paths["peak_frames"] / "batch-manifest.json"
    manifest_path.write_text(json.dumps({"firstBatchCsvGate": {"status": state,
        "materialIds": ["m-1", "m-2"], "message": "review first batch"}}))
    manifest_before = manifest_path.read_bytes()
    with pytest.raises(workflow.StandardWorkflowError, match="first batch|首批"):
        workflow.prepare_standard_run(request, tmp_path,
            export_main=lambda *_args: pytest.fail("must not repeat metadata selection"),
            extract_main=lambda *_args: pytest.fail("must not restart RPA"))
    assert paths["summary"].read_bytes() == summary_before
    assert manifest_path.read_bytes() == manifest_before


def test_valid_csv_without_video_is_csv_success_but_not_selected(tmp_path, monkeypatch):
    args = SimpleNamespace(output_dir=str(tmp_path / "peak-frames"),
                           diagnosis_records_dir=str(tmp_path / "diagnosis-records"),
                           rpa_late_artifact_grace_sec=0)

    def fetch(_args, material_id, **_kwargs):
        csv_dir = tmp_path / "diagnosis-csv"
        csv_dir.mkdir()
        (csv_dir / f"{material_id}.csv").write_text(
            "recordType\x01materialId\x01videoUrl\nmaterial_info\x01m-1\x01\n")
        return {"rpaAvailable": False, "failureReason": "missing_video_url"}

    monkeypatch.setattr(pipeline, "_fetch_material_preflight_for_cli", fetch)
    result = pipeline._attempt_wave_rpa(args, pipeline._wave_entry({"materialId": "m-1"}, 1))
    assert result["rpaStatus"] == "rejected"
    assert result["rpaCsvVerified"] is True
    assert result["rpaResultStatus"] == "available"


def test_poll_timeout_keeps_remote_outcome_unknown(tmp_path):
    args = SimpleNamespace(output_dir=str(tmp_path / "peak-frames"),
                           rpa_late_artifact_grace_sec=0)
    result = pipeline._recover_wave_rpa_result(args, "m-1", {
        "rpaAvailable": False, "failureReason": "rpa_request_failed",
        "remoteTaskGroupId": "existing-task", "timeoutExceeded": True,
    }, started_at_epoch=0)
    assert result["rpaResultStatus"] == "unknown"
    assert result["remoteTaskGroupId"] == "existing-task"


@pytest.mark.parametrize("remote_status,terminal", [("failed", True), ("mystery", False)])
def test_gateway_failure_reports_whether_remote_task_is_terminal(remote_status, terminal):
    import test_peak_frames as fixtures
    helper = fixtures.RpaAdapterTests()
    helper.setUp()
    try:
        with pytest.raises(fixtures.rpa_adapter.RpaAdapterError) as caught:
            helper._fetch_terminal(status=remote_status, errors=[{"message": "diagnosis failed"}])
        assert caught.value.diagnostics.get("remoteTaskTerminal") is terminal
    finally:
        helper.tearDown()


@pytest.mark.parametrize("state,action", [("blocked", "check_parameters"),
                                         ("unconfirmed", "check_remote_status")])
def test_status_exposes_gate_instead_of_suggesting_new_rpa(tmp_path, state, action):
    (tmp_path / "peak-frames").mkdir()
    (tmp_path / "run-summary.json").write_text(json.dumps({"status": "running"}))
    (tmp_path / "peak-frames" / "batch-manifest.json").write_text(json.dumps({
        "firstBatchCsvGate": {"status": state, "message": "review first batch"}}))
    status = workflow_status.standard_run_status(tmp_path)
    assert status["nextAction"] == action
    assert status["firstBatchCsvGate"]["status"] == state
    assert status["workflowStatus"] == "paused"
