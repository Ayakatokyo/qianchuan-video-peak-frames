"""Offline interruption windows that must never submit an existing RPA twice."""

import json
import multiprocessing
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extract_peak_frames as pipeline
from rpa_artifact_receipts import publish_receipt


def resume_fixture(root, **updates):
    args = SimpleNamespace(resume=True, output_dir=str(root / "peak-frames"),
                           diagnosis_records_dir=str(root / "diagnosis-records"))
    candidates = [{"materialId": "m-1"}]
    entries = {0: pipeline._wave_entry(candidates[0], 1)}
    manifest = {"materials": [dict(entries[0], **updates)]}
    return args, candidates, entries, manifest


@pytest.mark.parametrize("status", ["pending", "selected", "submitted"])
def test_remote_receipt_is_reused_even_when_manifest_is_older(tmp_path, status):
    publish_receipt(tmp_path / "rpa-receipts", material_id="m-1",
                    client_attempt_id="original-attempt", state="submitted",
                    remote_task_group_id="existing-group")
    args, candidates, entries, manifest = resume_fixture(tmp_path, rpaStatus=status)
    with patch.object(pipeline, "_load_wave_resume_manifest", return_value=manifest):
        _submitted, selected, rejected, _cancelled, _queue = pipeline._resume_wave_state(
            args, candidates, entries)
    assert selected == rejected == {}
    assert entries[0].get("remoteTaskGroupId") == "existing-group"
    assert entries[0].get("rpaClientAttemptId") == "original-attempt"


@pytest.mark.parametrize("receipt_state", ["submitted", "malformed"])
def test_pending_manifest_with_submission_evidence_is_never_resubmitted(tmp_path, receipt_state):
    receipt_dir = tmp_path / "rpa-receipts"
    if receipt_state == "submitted":
        publish_receipt(receipt_dir, material_id="m-1", client_attempt_id="old-attempt",
                        state="submitted")
    else:
        receipt_dir.mkdir()
        (receipt_dir / "m-1.json").write_text("truncated", encoding="utf-8")
    args, candidates, entries, manifest = resume_fixture(tmp_path)
    with patch.object(pipeline, "_load_wave_resume_manifest", return_value=manifest):
        _submitted, _selected, rejected, _cancelled, _queue = pipeline._resume_wave_state(
            args, candidates, entries)
    assert set(rejected) == {0}
    assert entries[0]["rpaStatus"] == "rejected"


def test_selected_material_without_artifacts_is_not_new_candidate(tmp_path):
    args, candidates, entries, manifest = resume_fixture(tmp_path, rpaStatus="selected")
    with patch.object(pipeline, "_load_wave_resume_manifest", return_value=manifest):
        _submitted, _selected, rejected, _cancelled, _queue = pipeline._resume_wave_state(
            args, candidates, entries)
    assert set(rejected) == {0}


def test_resume_marks_unfinished_attempt_interrupted_and_preserves_retry(tmp_path):
    records_dir = tmp_path / "diagnosis-records"
    records_dir.mkdir()
    (records_dir / "m-1.json").write_text(json.dumps([
        {"recordType": "material_info", "materialId": "m-1",
         "videoUrl": "https://example.test/video.mp4"}]), encoding="utf-8")
    args, candidates, entries, manifest = resume_fixture(
        tmp_path, rpaStatus="selected", rpaCsvVerified=True, postprocessStatus="running",
        postprocessAttemptCount=2, postprocessAttempts=[
            {"phase": "parallel", "status": "failed"},
            {"phase": "parallel", "status": "running"},
        ])
    with patch.object(pipeline, "_load_wave_resume_manifest", return_value=manifest):
        _submitted, selected, _rejected, _cancelled, queue = pipeline._resume_wave_state(
            args, candidates, entries)
    assert queue == [0]
    assert selected[0]["postprocessAttempts"][-1]["status"] == "interrupted"
    assert selected[0]["postprocessAttemptCount"] == 2


def _local_batch_args(root):
    args = pipeline.build_parser().parse_args([
        "--output-dir", str(Path(root) / "peak-frames"), "--target-top-n", "1",
        "--candidate-top-n", "1", "--shop-id", "offline-shop", "--resume",
    ])
    args.run_id = "offline-crash-recovery"
    return args


def _crash_after_download(root):
    def download(_url, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"offline retained video")

    def frames(*_args, **_kwargs):
        os._exit(23)

    with patch.object(pipeline, "download_video", side_effect=download), patch.object(
        pipeline, "process_records", side_effect=frames
    ), patch.object(pipeline, "_attempt_wave_rpa", side_effect=RuntimeError("unexpected RPA")):
        pipeline._run_wave_rpa_batch(_local_batch_args(root), {"materials": [{"materialId": "m-1"}]})


def test_process_death_after_download_reuses_video_on_resume(tmp_path):
    records_dir = tmp_path / "diagnosis-records"
    records_dir.mkdir()
    records_path = records_dir / "m-1.json"
    records_path.write_text(json.dumps([{
        "recordType": "material_info", "materialId": "m-1",
        "videoUrl": "https://example.test/video.mp4",
    }]), encoding="utf-8")
    csv_path = tmp_path / "m-1.csv"
    csv_path.write_text("retained offline CSV", encoding="utf-8")
    publish_receipt(tmp_path / "rpa-receipts", material_id="m-1", client_attempt_id="old-attempt",
                    state="records_persisted", csv_path=csv_path, records_path=records_path)

    child = multiprocessing.get_context("spawn").Process(target=_crash_after_download, args=(str(tmp_path),))
    child.start()
    child.join(timeout=10)
    try:
        assert not child.is_alive()
        assert child.exitcode == 23
    finally:
        if child.is_alive():
            child.kill()
            child.join(timeout=2)
    manifest_path = tmp_path / "peak-frames" / "batch-manifest.json"
    interrupted = json.loads(manifest_path.read_text(encoding="utf-8"))["materials"][0]
    assert interrupted["videoSha256"]
    assert interrupted["postprocessAttempts"][-1]["status"] == "running"

    result_dir = tmp_path / "peak-frames" / "m-1"
    with patch.object(pipeline, "download_video", side_effect=AssertionError("video must be reused")), \
         patch.object(pipeline, "_attempt_wave_rpa", side_effect=AssertionError("RPA must be reused")), \
         patch.object(pipeline, "process_records", return_value=(
             result_dir, result_dir / "summary.json", result_dir / "summary.md")):
        _path, _result, failures = pipeline._run_wave_rpa_batch(
            _local_batch_args(tmp_path), {"materials": [{"materialId": "m-1"}]})
    assert not failures
    resumed = json.loads(manifest_path.read_text(encoding="utf-8"))["materials"][0]
    assert [attempt["status"] for attempt in resumed["postprocessAttempts"]] == ["interrupted", "succeeded"]
    assert resumed["videoSha256"] == interrupted["videoSha256"]
