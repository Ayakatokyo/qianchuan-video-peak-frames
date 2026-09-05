"""Standard orchestration for a complete Qianchuan material-analysis run."""

from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path

import export_top_materials
import extract_peak_frames
from diagnostic_analysis_lib import prepare_diagnostic_analysis
from final_report_lib import generate_final_report, validate_standard_ai_analysis
from qianchuan_report_client import fetch_material_report_metadata
from report_renderer import create_report_archive
from batch_runtime import (
    BatchLockError,
    acquire_run_lock,
    mark_report_ready,
    read_json,
    write_json_atomically,
)
from top_material_roi_lib import write_top_material_roi_facts


SUMMARY_NAME = "run-summary.json"
READY_MATERIALS_NAME = "top_materials_rpa_ready.json"


@dataclass(frozen=True)
class WorkflowRequest:
    start_date: str
    end_date: str
    top_n: int | None
    advertiser_id: str
    shop_id: str


class StandardWorkflowError(RuntimeError):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


class WorkflowLockError(StandardWorkflowError):
    pass


def workflow_paths(output_dir):
    root = Path(output_dir)
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
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def workflow_lock_path(request, output_dir, shop_id):
    return Path(output_dir).parent / ".qianchuan-workflow-locks" / (
        _workflow_request_key(request, shop_id) + ".lock"
    )


def acquire_workflow_lock(request, output_dir, shop_id=None):
    resolved_shop_id = request.shop_id if shop_id is None else shop_id
    paths = workflow_paths(output_dir)
    try:
        return acquire_run_lock(
            workflow_lock_path(request, output_dir, resolved_shop_id),
            paths["summary"],
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
             analysis_unit_manifest=None, report=None, report_archive=None,
             failure=None, active_stage=None):
    root = paths["root"]
    request_summary = {
        "startDate": request.start_date,
        "endDate": request.end_date,
        "topN": request.top_n,
        "advertiserId": request.advertiser_id,
        "shopId": request.shop_id,
    } if isinstance(request, WorkflowRequest) else None
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
                _relative(root, Path(report).parent / "highlight-frames.xlsx")
                if report and (Path(report).parent / "highlight-frames.xlsx").is_file()
                else None
            ),
            "reportArchive": _relative(root, report_archive) if report_archive else None,
        },
        "failure": failure,
    }


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


def standard_run_status(output_dir):
    paths = workflow_paths(output_dir)
    summary = read_json(paths["summary"])
    if not isinstance(summary, dict):
        summary = {}
    manifest = read_json(paths["peak_frames"] / "batch-manifest.json")
    stage_counts = manifest.get("stageCounts", {}) if isinstance(manifest, dict) else {}
    if not isinstance(stage_counts, dict):
        stage_counts = {}

    lock = None
    summary_path = str(paths["summary"].resolve())
    lock_root = paths["root"].parent / ".qianchuan-workflow-locks"
    if lock_root.is_dir():
        for lock_path in lock_root.glob("*.lock"):
            payload = read_json(lock_path)
            if not isinstance(payload, dict):
                continue
            manifest_path = payload.get("manifestPath")
            if manifest_path and str(Path(manifest_path).resolve()) == summary_path:
                lock = payload
                break

    return {
        "workflowStatus": summary.get("status", "not_started"),
        "activeStage": summary.get("activeStage"),
        "lock": lock,
        "rpaRequestCount": stage_counts.get("rpaRequestCount", 0),
        "rpaAcceptedCount": stage_counts.get("rpaAcceptedCount", 0),
        "rpaSelectedCount": stage_counts.get("rpaSelectedCount", 0),
        "recordsPersistedCount": stage_counts.get("recordsPersistedCount", 0),
        "postprocessSucceededCount": stage_counts.get("postprocessSucceededCount", 0),
        "reportReadyCount": stage_counts.get("reportReadyCount", 0),
    }


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
                "--date-range-type", "CUSTOM",
                "--custom-start-date", request.start_date,
                "--custom-end-date", request.end_date,
                "--diagnosis-records-dir", str(paths["records"]),
                "--output-dir", str(paths["peak_frames"]),
            ]
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


def validate_analysis(output_dir, analysis_json, *, validate=validate_standard_ai_analysis):
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
        if summary.get("status") != "analysis_pending":
            raise StandardWorkflowError("analysis", "run is not awaiting AI analysis")
        analysis_path = Path(analysis_json)
        if not analysis_path.is_file():
            raise StandardWorkflowError("analysis", "analysis JSON is missing")
        details = validate(
            top_materials_json=paths["peak_frames"] / READY_MATERIALS_NAME,
            peak_output_dir=paths["peak_frames"],
            diagnosis_records_dir=paths["records"],
            analysis_json=analysis_path,
        )
    except StandardWorkflowError:
        raise
    except Exception as exc:
        raise StandardWorkflowError("analysis", str(exc)) from exc
    return {"analysisStatus": "valid", **details}


def finalize_standard_run(output_dir, analysis_json, *, render_report=generate_final_report,
                          mark_ready=mark_report_ready):
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
        if summary.get("status") != "analysis_pending":
            raise StandardWorkflowError("report", "run is not awaiting AI analysis")
        analysis_path = Path(analysis_json)
        if not analysis_path.is_file():
            raise StandardWorkflowError("report", "analysis JSON is missing")
        with acquire_workflow_lock(request, output_dir):
            _write_running(request, paths, "report")
            try:
                data_path, html_path, _data = render_report(
                    top_materials_json=paths["peak_frames"] / READY_MATERIALS_NAME,
                    peak_output_dir=paths["peak_frames"],
                    diagnosis_records_dir=paths["records"],
                    output_dir=paths["report"],
                    analysis_json=analysis_path,
                    copy_assets=True,
                    require_ai_quality=True,
                    require_local_video_assets=True,
                )
            except Exception as exc:
                raise StandardWorkflowError("report", str(exc)) from exc
            if not mark_ready(paths["peak_frames"] / "batch-manifest.json", data_path, html_path):
                raise StandardWorkflowError("report", "final report failed manifest validation")
            try:
                archive_path = create_report_archive(paths["report"])
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
                report_archive=archive_path,
            )
            write_run_summary(paths["summary"], completed)
            return completed
    except WorkflowLockError:
        raise
    except StandardWorkflowError as exc:
        status = "analysis_pending" if (
            summary.get("status") == "analysis_pending" and exc.stage == "report"
        ) else "failed"
        _write_failure(request, paths, exc.stage, str(exc), status=status)
        raise
    except Exception as exc:
        _write_failure(request, paths, "report", str(exc), status="analysis_pending")
        raise StandardWorkflowError("report", str(exc)) from exc
