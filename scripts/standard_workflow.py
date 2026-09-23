"""Standard orchestration for a complete Qianchuan material-analysis run."""

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
import tempfile

from analysis_handoff import assemble_analysis, file_hash, inspect_inputs, read_object
from analysis_quality import finding
from frame_review import start_frame_review, validate_frame_review
from video_resource_cleanup import cleanup_original_videos

import export_top_materials
import extract_peak_frames
from diagnostic_analysis_lib import prepare_diagnostic_analysis
from final_report_lib import (
    generate_final_report,
    mark_report_build_stage,
    report_build_stage_complete,
    validate_standard_ai_analysis,
)
from qianchuan_report_client import fetch_material_report_metadata
from report_renderer import create_report_archive
from batch_runtime import (
    BatchLockError,
    acquire_run_lock,
    mark_report_ready,
    read_json,
    run_lock_state,
    write_json_atomically,
)
from repair_manifest import repair_batch as repair_retained_batch
from top_material_roi_lib import write_top_material_roi_facts
from top_material_roi_lib import build_top_material_roi_payload
from workflow_status import standard_run_status


SUMMARY_NAME = "run-summary.json"
READY_MATERIALS_NAME = "top_materials_rpa_ready.json"


@dataclass(frozen=True)
class WorkflowRequest:
    start_date: str
    end_date: str
    top_n: int | None
    advertiser_id: str
    shop_id: str
    rpa_shop_id: str | None = None


class StandardWorkflowError(RuntimeError):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


class WorkflowLockError(StandardWorkflowError):
    pass


def workflow_paths(output_dir):
    root = Path(output_dir).absolute()
    return {
        "root": root,
        "source_data": root / "source-data",
        "top_materials": root / "top_materials.json",
        "asset_facts": root / "asset-material-facts-v1.json",
        "top_material_roi_facts": root / "peak-frames" / "top-material-roi-facts-v1.json",
        "peak_frames": root / "peak-frames",
        "records": root / "diagnosis-records",
        "analysis": root / "diagnostic-analysis",
        "report": root / "final-report",
        "summary": root / SUMMARY_NAME,
    }


def _workflow_request_key(request, shop_id):
    payload = json.dumps({
        "advertiserId": request.advertiser_id,
        "startDate": request.start_date,
        "endDate": request.end_date,
        "topN": request.top_n,
        "shopId": shop_id,
        "rpaShopId": request.rpa_shop_id,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def workflow_lock_path(request, output_dir, shop_id):
    return Path(output_dir).parent / ".qianchuan-workflow-locks" / (
        _workflow_request_key(request, shop_id) + ".lock"
    )


def acquire_workflow_lock(
    request, output_dir, shop_id=None, *, reclaim_stale=False
):
    resolved_shop_id = request.shop_id if shop_id is None else shop_id
    paths = workflow_paths(output_dir)
    try:
        return acquire_run_lock(
            workflow_lock_path(request, output_dir, resolved_shop_id),
            paths["summary"],
            reclaim_stale=reclaim_stale,
        )
    except BatchLockError as exc:
        raise WorkflowLockError("workflow", f"workflow already running: {exc}") from exc


write_run_summary = write_json_atomically


def _validate_request(request, *, require_top_n=True):
    if not isinstance(request, WorkflowRequest):
        raise StandardWorkflowError("request", "request must be a WorkflowRequest")
    try:
        start_date = date.fromisoformat(request.start_date)
        end_date = date.fromisoformat(request.end_date)
    except (TypeError, ValueError) as exc:
        raise StandardWorkflowError("request", "dates must use YYYY-MM-DD") from exc
    if start_date > end_date:
        raise StandardWorkflowError("request", "start_date must not be after end_date")
    if require_top_n and (type(request.top_n) is not int or request.top_n <= 0):
        raise StandardWorkflowError("request", "top_n must be a positive integer")
    if not require_top_n and request.top_n is not None and (
        type(request.top_n) is not int or request.top_n <= 0
    ):
        raise StandardWorkflowError("request", "top_n must be a positive integer when provided")
    if not str(request.advertiser_id or "").strip():
        raise StandardWorkflowError("request", "advertiser_id is required")
    if not str(request.shop_id or "").strip():
        raise StandardWorkflowError("request", "shop_id is required")


def _relative(root, path):
    return str(Path(path).resolve().relative_to(Path(root).resolve()))


def _summary(request, paths, *, status, diagnostic_prompt=None,
             analysis_unit_manifest=None, report=None, highlight_export=None,
             report_archive=None,
             failure=None, active_stage=None):
    root = paths["root"]
    request_summary = {
        "startDate": request.start_date,
        "endDate": request.end_date,
        "topN": request.top_n,
        "advertiserId": request.advertiser_id,
        "shopId": request.shop_id,
    } if isinstance(request, WorkflowRequest) else None
    if request_summary is not None and request.rpa_shop_id:
        request_summary["rpaShopId"] = request.rpa_shop_id
    return {
        "schemaVersion": 2,
        "status": status,
        "activeStage": active_stage,
        "request": request_summary,
        "artifacts": {
            "sourceData": _relative(root, paths["source_data"]),
            "topMaterials": _relative(root, paths["top_materials"]),
            "assetMaterialFacts": _relative(root, paths["asset_facts"]),
            "topMaterialRoiFacts": _relative(root, paths["top_material_roi_facts"]),
            "readyMaterials": _relative(root, paths["peak_frames"] / READY_MATERIALS_NAME),
            "diagnosticPrompt": _relative(root, diagnostic_prompt) if diagnostic_prompt else None,
            "analysisUnitManifest": (
                _relative(root, analysis_unit_manifest)
                if analysis_unit_manifest else None
            ),
            "report": _relative(root, report) if report else None,
            "reportDisplayFacts": (
                _relative(root, Path(report).parent / "report-display-facts.json")
                if report and (Path(report).parent / "report-display-facts.json").is_file()
                else None
            ),
            "highlightExport": (
                _relative(root, highlight_export) if highlight_export else None
            ),
            "reportArchive": _relative(root, report_archive) if report_archive else None,
        },
        "failure": failure,
    }


def _request_mismatch_message(saved_request, current_request, prefix):
    if not isinstance(saved_request, dict):
        return f"{prefix}: saved request is missing or invalid"
    if not isinstance(current_request, dict):
        return f"{prefix}: current request is missing or invalid"

    missing = object()

    def display(value):
        if value is missing:
            return "<missing>"
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    differences = []
    for field in sorted(set(saved_request) | set(current_request)):
        saved_value = saved_request.get(field, missing)
        current_value = current_request.get(field, missing)
        if saved_value != current_value:
            differences.append(
                f"{field}: saved={display(saved_value)}, current={display(current_value)}"
            )
    return f"{prefix}: " + "; ".join(differences)


def _run_stage(stage, entrypoint, arguments):
    try:
        status = entrypoint(arguments)
    except Exception as exc:
        raise StandardWorkflowError(stage, str(exc)) from exc
    if status != 0:
        raise StandardWorkflowError(stage, f"{stage} command exited with status {status}")


def _write_failure(request, paths, stage, message, *, status="failed"):
    summary = _summary(
        request,
        paths,
        status=status,
        failure={"stage": stage, "message": message},
    )
    write_run_summary(paths["summary"], summary)


def _write_running(request, paths, stage):
    write_run_summary(paths["summary"], _summary(
        request,
        paths,
        status="running",
        active_stage=stage,
    ))


def validate_ready_materials(paths, top_n):
    ready = read_json(paths["peak_frames"] / READY_MATERIALS_NAME)
    materials = ready.get("materials") if isinstance(ready, dict) else None
    if not isinstance(materials, list):
        raise StandardWorkflowError("facts", "ready materials are missing or invalid")
    ready_ids = [
        str(material.get("materialId") or "").strip()
        for material in materials if isinstance(material, dict)
    ]
    if (
        len(ready_ids) != top_n
        or any(not material_id for material_id in ready_ids)
        or len(set(ready_ids)) != top_n
    ):
        raise StandardWorkflowError("facts", "ready materials must contain exactly Top N unique IDs")

    manifest = read_json(paths["peak_frames"] / "batch-manifest.json")
    selected = manifest.get("selectedMaterials") if isinstance(manifest, dict) else None
    if not isinstance(selected, list):
        raise StandardWorkflowError("facts", "ready materials manifest is missing selected materials")

    successful = [
        entry for entry in selected
        if isinstance(entry, dict)
        and entry.get("rpaStatus") == "selected"
        and entry.get("postprocessStatus") == "succeeded"
        and str(entry.get("materialId") or "").strip()
    ]
    successful.sort(key=lambda entry: (
        entry.get("rank") if type(entry.get("rank")) is int else float("inf"),
        str(entry.get("materialId")),
    ))
    expected_ids = [str(entry["materialId"]).strip() for entry in successful[:top_n]]
    if len(expected_ids) != top_n or set(ready_ids) != set(expected_ids):
        raise StandardWorkflowError(
            "facts", "ready materials do not match the Top N successful manifest entries"
        )

    receipt = read_json(paths["peak_frames"] / "batch-verification.json")
    receipt_ids = receipt.get("materialIds") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or receipt.get("valid") is not True
        or not isinstance(receipt_ids, list)
        or len(receipt_ids) != top_n
        or set(str(material_id).strip() for material_id in receipt_ids) != set(expected_ids)
    ):
        raise StandardWorkflowError(
            "facts", "ready materials verification receipt does not match Top N"
        )
    return ready


def prepare_standard_run(request, output_dir, *, export_main=export_top_materials.main,
                         extract_main=extract_peak_frames.main,
                         prepare_facts=prepare_diagnostic_analysis):
    paths = workflow_paths(output_dir)
    try:
        _validate_request(request)
        candidate_top_n = request.top_n * 3
        with acquire_workflow_lock(request, output_dir):
            _write_running(request, paths, "selection")
            _run_stage("selection", export_main, [
                "--shop-id", request.shop_id,
                "--start-date", request.start_date,
                "--end-date", request.end_date,
                "--advertiser-id", request.advertiser_id,
                "--top-n", str(candidate_top_n),
                "--output", str(paths["top_materials"]),
                "--asset-facts-output", str(paths["asset_facts"]),
                "--source-output-dir", str(paths["source_data"]),
            ])
            batch_arguments = [
                "--source", "rpa",
                "--top-materials-json", str(paths["top_materials"]),
                "--target-top-n", str(request.top_n),
                "--candidate-top-n", str(candidate_top_n),
                "--params-json", json.dumps({
                    "date_type": "CUSTOM",
                    "custom_start_date": request.start_date,
                    "custom_end_date": request.end_date,
                }, ensure_ascii=False, separators=(",", ":")),
                "--diagnosis-records-dir", str(paths["records"]),
                "--output-dir", str(paths["peak_frames"]),
            ]
            if request.rpa_shop_id:
                batch_arguments.extend(["--shop-id", request.rpa_shop_id])
            _write_running(request, paths, "diagnosis_batch")
            _run_stage("diagnosis_batch", extract_main, batch_arguments)
            _write_running(request, paths, "verification")
            _run_stage("verification", extract_main, [*batch_arguments, "--verify"])
            _write_running(request, paths, "facts")
            validate_ready_materials(paths, request.top_n)
            write_top_material_roi_facts(
                paths["asset_facts"],
                paths["peak_frames"] / READY_MATERIALS_NAME,
                paths["top_material_roi_facts"],
            )
            prompt_path, _analysis = prepare_facts(
                top_materials_json=paths["peak_frames"] / READY_MATERIALS_NAME,
                diagnosis_records_dir=paths["records"],
                peak_output_dir=paths["peak_frames"],
                top_material_roi_facts_path=paths["top_material_roi_facts"],
                output_dir=paths["analysis"],
            )
            if prepare_facts is prepare_diagnostic_analysis:
                start_frame_review(paths["analysis"])
            summary = _summary(
                request,
                paths,
                status="analysis_pending",
                diagnostic_prompt=prompt_path,
                analysis_unit_manifest=(
                    paths["analysis"]
                    / "analysis-units"
                    / "analysis-unit-manifest.json"
                ),
            )
            write_run_summary(paths["summary"], summary)
            return summary
    except WorkflowLockError:
        raise
    except StandardWorkflowError as exc:
        _write_failure(request, paths, exc.stage, str(exc))
        raise
    except Exception as exc:
        _write_failure(request, paths, "facts", str(exc))
        raise StandardWorkflowError("facts", str(exc)) from exc


def resume_standard_run(
    request,
    output_dir,
    *,
    extract_main=extract_peak_frames.main,
    repair_batch=repair_retained_batch,
    prepare_facts=prepare_diagnostic_analysis,
):
    """Resume an interrupted standard run without repeating report selection."""
    paths = workflow_paths(output_dir)
    recovery_started = False
    try:
        _validate_request(request)
        expected_request = _summary(request, paths, status="")["request"]
        old = read_json(paths["summary"])
        saved_request = old.get("request") if isinstance(old, dict) else None
        if saved_request != expected_request:
            raise StandardWorkflowError(
                "recovery",
                _request_mismatch_message(
                    saved_request,
                    expected_request,
                    "saved run request does not match resume parameters",
                ),
            )
        if old.get("status") in {"analysis_pending", "complete"}:
            raise StandardWorkflowError(
                "recovery", f"run is already {old.get('status')}; collection resume is not needed"
            )
        if not paths["top_materials"].is_file():
            raise StandardWorkflowError(
                "recovery", "top_materials.json is missing; interrupted selection cannot be resumed"
            )
        batch_state, _batch_lock = run_lock_state(
            paths["peak_frames"] / ".batch-run.lock"
        )
        if batch_state == "active":
            raise StandardWorkflowError(
                "recovery", "diagnosis batch is still active; do not start another resume"
            )

        candidate_top_n = request.top_n * 3
        with acquire_workflow_lock(request, output_dir, reclaim_stale=True):
            recovery_started = True
            _write_running(request, paths, "recovery")
            # Reconcile CSV/records and paths once. The batch engine owns both
            # collection and postprocess so resume cannot bypass its barrier.
            repair_receipt = repair_batch(
                paths["peak_frames"],
                diagnosis_records_dir=paths["records"],
                diagnosis_csv_dir=paths["root"] / "diagnosis-csv",
                top_materials_json=paths["top_materials"],
                records_only=True,
            )
            if repair_receipt.get("rpaCalls", 0) != 0 or repair_receipt.get("postprocessAttemptedCount", 0) != 0:
                raise StandardWorkflowError(
                    "recovery", "records-only repair attempted RPA or video postprocess"
                )
            if not repair_receipt.get("valid", False):
                repair_errors = repair_receipt.get("errors") or []
                detail = "; ".join(str(item) for item in repair_errors[:3])
                if len(repair_errors) > 3:
                    detail += f"; and {len(repair_errors) - 3} more"
                raise StandardWorkflowError(
                    "recovery", "retained artifact repair is invalid" + (f": {detail}" if detail else "")
                )
            batch_arguments = [
                "--source", "rpa",
                "--top-materials-json", str(paths["top_materials"]),
                "--target-top-n", str(request.top_n),
                "--candidate-top-n", str(candidate_top_n),
                "--params-json", json.dumps({
                    "date_type": "CUSTOM",
                    "custom_start_date": request.start_date,
                    "custom_end_date": request.end_date,
                }, ensure_ascii=False, separators=(",", ":")),
                "--diagnosis-records-dir", str(paths["records"]),
                "--output-dir", str(paths["peak_frames"]),
            ]
            if request.rpa_shop_id:
                batch_arguments.extend(["--shop-id", request.rpa_shop_id])
            _write_running(request, paths, "diagnosis_batch")
            _run_stage("diagnosis_batch", extract_main, [*batch_arguments, "--resume"])
            _write_running(request, paths, "verification")
            _run_stage("verification", extract_main, [*batch_arguments, "--verify"])
            _write_running(request, paths, "facts")
            validate_ready_materials(paths, request.top_n)
            write_top_material_roi_facts(
                paths["asset_facts"],
                paths["peak_frames"] / READY_MATERIALS_NAME,
                paths["top_material_roi_facts"],
            )
            prompt_path, _analysis = prepare_facts(
                top_materials_json=paths["peak_frames"] / READY_MATERIALS_NAME,
                diagnosis_records_dir=paths["records"],
                peak_output_dir=paths["peak_frames"],
                top_material_roi_facts_path=paths["top_material_roi_facts"],
                output_dir=paths["analysis"],
            )
            if prepare_facts is prepare_diagnostic_analysis:
                start_frame_review(paths["analysis"])
            summary = _summary(
                request,
                paths,
                status="analysis_pending",
                diagnostic_prompt=prompt_path,
                analysis_unit_manifest=(
                    paths["analysis"] / "analysis-units" / "analysis-unit-manifest.json"
                ),
            )
            summary["postprocessRecovery"] = {
                "rpaCalls": 0,
                "batchCount": 1,
                "attemptedCount": 0,
                "recordsOnly": True,
                "remainingCount": 0,
            }
            write_run_summary(paths["summary"], summary)
            return summary
    except WorkflowLockError:
        raise
    except StandardWorkflowError as exc:
        if recovery_started:
            _write_failure(request, paths, exc.stage, str(exc), status="interrupted")
        raise
    except Exception as exc:
        if recovery_started:
            _write_failure(request, paths, "recovery", str(exc), status="interrupted")
        raise StandardWorkflowError("recovery", str(exc)) from exc


def fetch_report_metadata(
    request,
    output_dir,
    *,
    fetch_metadata=fetch_material_report_metadata,
):
    paths = workflow_paths(output_dir)
    try:
        _validate_request(request, require_top_n=False)
        with acquire_workflow_lock(request, output_dir):
            _write_running(request, paths, "metadata")
            try:
                fetch_metadata(
                    request.shop_id,
                    request.advertiser_id,
                    request.start_date,
                    request.end_date,
                    output_dir=paths["source_data"],
                )
            except Exception as exc:
                raise StandardWorkflowError("metadata", str(exc)) from exc
            summary = _summary(request, paths, status="metadata_ready")
            write_run_summary(paths["summary"], summary)
            return summary
    except WorkflowLockError:
        raise
    except StandardWorkflowError as exc:
        _write_failure(request, paths, exc.stage, str(exc))
        raise
    except Exception as exc:
        _write_failure(request, paths, "metadata", str(exc))
        raise StandardWorkflowError("metadata", str(exc)) from exc


def _read_summary(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StandardWorkflowError("report", "run summary is unreadable") from exc
    if not isinstance(payload, dict):
        raise StandardWorkflowError("report", "run summary must be an object")
    return payload


def prepare_local_run(request, output_dir):
    """Complete only missing local facts; never invoke collection or media tools."""
    _validate_request(request)
    paths = workflow_paths(output_dir)
    with acquire_workflow_lock(request, output_dir):
        old = read_object(paths["summary"]) if paths["summary"].exists() else {}
        if old.get("status") in {"complete", "running"}:
            raise StandardWorkflowError("facts", "preserve this run; use an isolated copy for local recovery")
        current_request = _summary(request, paths, status="")["request"]
        if old.get("request") and old["request"] != current_request:
            raise StandardWorkflowError(
                "facts",
                _request_mismatch_message(
                    old["request"], current_request, "request does not match saved run"
                ),
            )
        ready = validate_ready_materials(paths, request.top_n)
        manifest = read_object(paths["peak_frames"] / "batch-manifest.json")
        selected = {m["materialId"]: m for m in manifest["selectedMaterials"]}
        asset = read_object(paths["asset_facts"])
        source = read_object(paths["top_materials"])
        for payload in (ready, asset, source):
            if (payload.get("actualStartDate") != request.start_date
                    or payload.get("actualEndDate") != request.end_date
                    or str(payload.get("filters", {}).get("advertiserId")) != request.advertiser_id):
                raise StandardWorkflowError("facts", "local source period/advertiser does not match request")
        if not isinstance(source.get("portfolioFacts"), dict) or source["portfolioFacts"] != ready.get("portfolioFacts"):
            raise StandardWorkflowError("facts", "ready materials must retain the full source portfolioFacts")
        for material in ready["materials"]:
            mid = material["materialId"]
            if not (paths["records"] / f"{mid}.json").is_file() or not (paths["peak_frames"] / mid / "summary.json").is_file():
                raise StandardWorkflowError("facts", f"missing landed records/summary: {mid}")
            video = Path(selected[mid].get("videoPath", ""))
            if not video.is_file() or video.is_symlink():
                raise StandardWorkflowError("facts", f"missing landed video: {mid}")
            if selected[mid].get("videoSha256") and file_hash(video) != selected[mid]["videoSha256"]:
                raise StandardWorkflowError("facts", f"landed video hash mismatch: {mid}")
        roi = build_top_material_roi_payload(asset, ready)
        if paths["top_material_roi_facts"].exists() and read_object(paths["top_material_roi_facts"]) != roi:
            raise StandardWorkflowError("facts", "existing ROI facts disagree with immutable sources")
        if paths["analysis"].exists():
            inputs = inspect_inputs(paths["analysis"])
        else:
            # Build off to the side; a failure cannot publish half a fact package.
            with tempfile.TemporaryDirectory(prefix=".local-facts-", dir=paths["root"]) as staging:
                staging = Path(staging)
                write_json_atomically(staging / "roi.json", roi)
                prepare_diagnostic_analysis(
                    top_materials_json=paths["peak_frames"] / READY_MATERIALS_NAME,
                    diagnosis_records_dir=paths["records"], peak_output_dir=paths["peak_frames"],
                    top_material_roi_facts_path=staging / "roi.json", output_dir=staging / "analysis",
                )
                inputs = inspect_inputs(staging / "analysis")
                if inputs["materialIds"] != [m["materialId"] for m in ready["materials"]]:
                    raise StandardWorkflowError("facts", "built facts do not match ready material order")
                expected_covers = {f"highlight:{m['materialId']}:zero_second" for m in ready["materials"]}
                if not expected_covers.issubset({frame["frameRef"] for frame in inputs["frames"]}):
                    raise StandardWorkflowError("facts", "landed materials require zero_second JPEGs")
                (staging / "analysis").rename(paths["analysis"])
        if inputs["materialIds"] != [m["materialId"] for m in ready["materials"]]:
            raise StandardWorkflowError("facts", "fact manifest does not match ready material order")
        if inputs["period"].get("actualStartDate") != request.start_date or inputs["period"].get("actualEndDate") != request.end_date:
            raise StandardWorkflowError("facts", "fact manifest period does not match request")
        if not paths["top_material_roi_facts"].exists():
            write_json_atomically(paths["top_material_roi_facts"], roi)
        start_frame_review(paths["analysis"])
        summary = _summary(request, paths, status="analysis_pending",
                           diagnostic_prompt=paths["analysis"] / "diagnostic-analysis-prompt.md",
                           analysis_unit_manifest=paths["analysis"] / "analysis-units" / "analysis-unit-manifest.json")
        summary["localSourceHashes"] = {str(p.relative_to(paths["root"])): file_hash(p)
            for p in (paths["asset_facts"], paths["top_materials"], paths["peak_frames"] / READY_MATERIALS_NAME)}
        write_run_summary(paths["summary"], summary)
        return summary


def validate_analysis(
    output_dir,
    analysis_json,
    *,
    quality_review=None,
    validate=validate_standard_ai_analysis,
    allow_report_resume=False,
):
    """Validate AI analysis without changing the run's retryable state."""
    paths = workflow_paths(output_dir)
    summary = _read_summary(paths["summary"])
    request_payload = summary.get("request")
    request = WorkflowRequest(
        start_date=request_payload.get("startDate", ""),
        end_date=request_payload.get("endDate", ""),
        top_n=request_payload.get("topN"),
        advertiser_id=request_payload.get("advertiserId", ""),
        shop_id=request_payload.get("shopId", ""),
    ) if isinstance(request_payload, dict) else None
    try:
        _validate_request(request)
        report_resume = (
            allow_report_resume
            and (summary.get("status") == "complete" or (
                summary.get("status") == "running" and summary.get("activeStage") == "report"
            ))
        )
        if summary.get("status") != "analysis_pending" and not report_resume:
            raise StandardWorkflowError("analysis", "run is not awaiting AI analysis")
        analysis_path = Path(analysis_json)
        if not analysis_path.is_file():
            raise StandardWorkflowError("analysis", "analysis JSON is missing")
        if validate is validate_standard_ai_analysis:
            if analysis_path.resolve().parent != paths["analysis"].resolve():
                raise StandardWorkflowError("analysis", "candidate must be next to this run's analysis-units")
            inspect_inputs(paths["analysis"])
            validate_frame_review(paths["analysis"])
        options = {"quality_review": quality_review} if quality_review is not None else {}
        details = validate(
            top_materials_json=paths["peak_frames"] / READY_MATERIALS_NAME,
            peak_output_dir=paths["peak_frames"],
            diagnosis_records_dir=paths["records"],
            analysis_json=analysis_path,
            **options,
        )
    except StandardWorkflowError:
        raise
    except Exception as exc:
        if Path(analysis_json).is_file():
            write_json_atomically(Path(analysis_json).with_suffix(".validation.json"), {
                "analysisStatus": "invalid", "analysisSha256": file_hash(analysis_json),
                "findings": getattr(exc, "findings", [finding("error", str(exc))]),
            })
        raise StandardWorkflowError("analysis", str(exc)) from exc
    result = {"analysisStatus": "valid", **details}
    write_json_atomically(Path(analysis_json).with_suffix(".validation.json"), result)
    return result


def finalize_standard_run(
    output_dir,
    analysis_json,
    *,
    quality_review=None,
    render_report=generate_final_report,
    mark_ready=mark_report_ready,
    video_cleanup=None,
    include_workbook=True,
    archive=False,
):
    paths = workflow_paths(output_dir)
    summary = _read_summary(paths["summary"])
    request_payload = summary.get("request")
    request = WorkflowRequest(
        start_date=request_payload.get("startDate", ""),
        end_date=request_payload.get("endDate", ""),
        top_n=request_payload.get("topN"),
        advertiser_id=request_payload.get("advertiserId", ""),
        shop_id=request_payload.get("shopId", ""),
    ) if isinstance(request_payload, dict) else None
    resumable_report = (
        summary.get("status") == "complete" or (
            summary.get("status") == "running" and summary.get("activeStage") == "report"
        )
    )
    report_started = False
    try:
        _validate_request(request)
        if video_cleanup in {"dry-run", "delete"} and not archive:
            raise StandardWorkflowError(
                "report",
                "video cleanup verification requires --archive",
            )
        if summary.get("status") != "analysis_pending" and not resumable_report:
            raise StandardWorkflowError("report", "run is not awaiting AI analysis")
        analysis_path = Path(analysis_json)
        if not analysis_path.is_file():
            raise StandardWorkflowError("report", "analysis JSON is missing")
        with acquire_workflow_lock(
            request, output_dir, reclaim_stale=resumable_report
        ):
            # The production path binds validation and rendering to the same bytes.
            candidate_hash = file_hash(analysis_path)
            frozen_inputs = None
            frozen_review_hash = None
            review_hash = file_hash(quality_review) if quality_review else None
            if render_report is generate_final_report:
                result = validate_analysis(
                    output_dir,
                    analysis_path,
                    quality_review=quality_review,
                    allow_report_resume=resumable_report,
                )
                frozen_inputs = inspect_inputs(paths["analysis"])
                frozen_review_hash = validate_frame_review(paths["analysis"])
                if result["analysisStatus"] != "valid":
                    raise StandardWorkflowError("report", "analysis has pending evidence review; see candidate .validation.json")
            report_started = True
            _write_running(request, paths, "report")
            try:
                if file_hash(analysis_path) != candidate_hash:
                    raise StandardWorkflowError("report", "analysis changed after validation")
                options = {"quality_review": quality_review} if quality_review is not None else {}
                data_path, html_path, _data = render_report(
                    top_materials_json=paths["peak_frames"] / READY_MATERIALS_NAME,
                    peak_output_dir=paths["peak_frames"],
                    diagnosis_records_dir=paths["records"],
                    output_dir=paths["report"],
                    analysis_json=analysis_path,
                    copy_assets=True,
                    require_ai_quality=True,
                    require_local_video_assets=True,
                    include_workbook=include_workbook,
                    **options,
                )
                # Keep only small receipt fields before rereading facts and report JSON.
                report_input_sha256 = _data.get("reportBuildInputSha256")
                analysis_quality = _data.get("analysisQuality", {})
                del _data
                if file_hash(analysis_path) != candidate_hash:
                    raise StandardWorkflowError("report", "analysis changed during rendering")
                if frozen_inputs is not None and inspect_inputs(paths["analysis"]) != frozen_inputs:
                    raise StandardWorkflowError("report", "facts, prompt or JPEG changed during rendering")
                if quality_review and file_hash(quality_review) != review_hash:
                    raise StandardWorkflowError("report", "quality review changed during rendering")
                if frozen_inputs is not None and validate_frame_review(paths["analysis"]) != frozen_review_hash:
                    raise StandardWorkflowError("report", "frame review changed during rendering")
            except Exception as exc:
                raise StandardWorkflowError("report", str(exc)) from exc
            if not mark_ready(paths["peak_frames"] / "batch-manifest.json", data_path, html_path):
                raise StandardWorkflowError("report", "final report failed manifest validation")
            archive_path = None
            if archive:
                expected_archive = paths["report"].parent / f"{paths['report'].name}.zip"
                try:
                    if report_input_sha256 and report_build_stage_complete(
                        paths["report"],
                        "archive",
                        report_input_sha256,
                        (expected_archive,),
                    ):
                        archive_path = expected_archive
                    else:
                        archive_path = create_report_archive(paths["report"])
                        if report_input_sha256:
                            mark_report_build_stage(
                                paths["report"],
                                report_input_sha256,
                                "archive",
                                artifact=str(archive_path),
                            )
                except Exception as exc:
                    raise StandardWorkflowError("report", str(exc)) from exc
            analysis_unit_manifest = (
                paths["analysis"] / "analysis-units" / "analysis-unit-manifest.json"
            )
            completed = _summary(
                request,
                paths,
                status="complete",
                diagnostic_prompt=paths["analysis"] / "diagnostic-analysis-prompt.md",
                analysis_unit_manifest=(
                    analysis_unit_manifest if analysis_unit_manifest.is_file() else None
                ),
                report=html_path,
                highlight_export=(
                    paths["report"] / "highlight-frames.xlsx"
                    if include_workbook
                    and (paths["report"] / "highlight-frames.xlsx").is_file()
                    else None
                ),
                report_archive=archive_path,
            )
            completed["analysisQuality"] = analysis_quality
            write_run_summary(paths["summary"], completed)
            mode = video_cleanup or "keep"
            try:
                cleanup_original_videos(output_dir, mode=mode)
            except Exception as exc:
                # Delivery is already complete. A cleanup/receipt failure must
                # never rewind the run into AI analysis or trigger rerendering.
                completed['videoResourceCleanup'] = {'status': 'partial', 'mode': mode, 'error': str(exc)}
                write_run_summary(paths['summary'], completed)
            return _read_summary(paths["summary"])
    except WorkflowLockError:
        raise
    except StandardWorkflowError as exc:
        if summary.get("status") == "complete" and not report_started:
            raise
        status = "analysis_pending" if (
            (summary.get("status") == "analysis_pending" or resumable_report)
            and exc.stage in {"analysis", "report"}
        ) else "failed"
        _write_failure(request, paths, exc.stage, str(exc), status=status)
        raise
    except Exception as exc:
        _write_failure(request, paths, "report", str(exc), status="analysis_pending")
        raise StandardWorkflowError("report", str(exc)) from exc
