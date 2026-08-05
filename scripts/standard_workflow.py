"""Standard orchestration for a complete Qianchuan material-analysis run."""

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import tempfile

import export_top_materials
import extract_peak_frames
from diagnostic_analysis_lib import prepare_diagnostic_analysis
from final_report_lib import generate_final_report
from batch_runtime import BatchLockError, acquire_run_lock, mark_report_ready


SUMMARY_NAME = "run-summary.json"
READY_MATERIALS_NAME = "top_materials_rpa_ready.json"


@dataclass(frozen=True)
class WorkflowRequest:
    start_date: str
    end_date: str
    top_n: int
    advertiser_id: str


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
        "top_materials": root / "top_materials.json",
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
    resolved_shop_id = (
        os.environ.get("QIANCHUAN_RPA_SHOP_ID", "").strip()
        if shop_id is None else shop_id
    )
    paths = workflow_paths(output_dir)
    try:
        return acquire_run_lock(
            workflow_lock_path(request, output_dir, resolved_shop_id),
            paths["summary"],
        )
    except BatchLockError as exc:
        raise WorkflowLockError("workflow", f"workflow already running: {exc}") from exc


def write_run_summary(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _validate_request(request):
    if not isinstance(request, WorkflowRequest):
        raise StandardWorkflowError("request", "request must be a WorkflowRequest")
    try:
        start_date = date.fromisoformat(request.start_date)
        end_date = date.fromisoformat(request.end_date)
    except (TypeError, ValueError) as exc:
        raise StandardWorkflowError("request", "dates must use YYYY-MM-DD") from exc
    if start_date > end_date:
        raise StandardWorkflowError("request", "start_date must not be after end_date")
    if type(request.top_n) is not int or request.top_n <= 0:
        raise StandardWorkflowError("request", "top_n must be a positive integer")
    if not str(request.advertiser_id or "").strip():
        raise StandardWorkflowError("request", "advertiser_id is required")


def _relative(root, path):
    return str(Path(path).relative_to(root))


def _summary(request, paths, *, status, diagnostic_data=None, diagnostic_prompt=None,
             report=None, failure=None):
    root = paths["root"]
    request_summary = {
        "startDate": request.start_date,
        "endDate": request.end_date,
        "topN": request.top_n,
        "advertiserId": request.advertiser_id,
    } if isinstance(request, WorkflowRequest) else None
    return {
        "schemaVersion": 1,
        "status": status,
        "request": request_summary,
        "artifacts": {
            "topMaterials": _relative(root, paths["top_materials"]),
            "readyMaterials": _relative(root, paths["peak_frames"] / READY_MATERIALS_NAME),
            "diagnosticData": _relative(root, diagnostic_data) if diagnostic_data else None,
            "diagnosticPrompt": _relative(root, diagnostic_prompt) if diagnostic_prompt else None,
            "report": _relative(root, report) if report else None,
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


def prepare_standard_run(request, output_dir, *, export_main=export_top_materials.main,
                         extract_main=extract_peak_frames.main,
                         prepare_facts=prepare_diagnostic_analysis):
    paths = workflow_paths(output_dir)
    try:
        _validate_request(request)
        candidate_top_n = request.top_n * 3
        shop_id = os.environ.get("QIANCHUAN_RPA_SHOP_ID", "").strip()
        with acquire_workflow_lock(request, output_dir, shop_id):
            _run_stage("selection", export_main, [
                "--start-date", request.start_date,
                "--end-date", request.end_date,
                "--advertiser-id", request.advertiser_id,
                "--top-n", str(candidate_top_n),
                "--output", str(paths["top_materials"]),
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
            if shop_id:
                batch_arguments.extend(["--shop-id", shop_id])
            _run_stage("diagnosis_batch", extract_main, batch_arguments)
            _run_stage("verification", extract_main, [*batch_arguments, "--verify"])
            data_path, prompt_path, _analysis = prepare_facts(
                top_materials_json=paths["peak_frames"] / READY_MATERIALS_NAME,
                diagnosis_records_dir=paths["records"],
                peak_output_dir=paths["peak_frames"],
                output_dir=paths["analysis"],
            )
            summary = _summary(
                request,
                paths,
                status="analysis_pending",
                diagnostic_data=data_path,
                diagnostic_prompt=prompt_path,
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


def _read_summary(path):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StandardWorkflowError("report", "run summary is unreadable") from exc
    if not isinstance(payload, dict):
        raise StandardWorkflowError("report", "run summary must be an object")
    return payload


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
    ) if isinstance(request_payload, dict) else None
    try:
        _validate_request(request)
        if summary.get("status") != "analysis_pending":
            raise StandardWorkflowError("report", "run is not awaiting AI analysis")
        analysis_path = Path(analysis_json)
        if not analysis_path.is_file():
            raise StandardWorkflowError("report", "analysis JSON is missing")
        with acquire_workflow_lock(request, output_dir):
            try:
                data_path, html_path, _data = render_report(
                    top_materials_json=paths["peak_frames"] / READY_MATERIALS_NAME,
                    peak_output_dir=paths["peak_frames"],
                    diagnosis_records_dir=paths["records"],
                    output_dir=paths["report"],
                    analysis_json=analysis_path,
                    copy_assets=True,
                    require_ai_quality=True,
                )
            except Exception as exc:
                raise StandardWorkflowError("report", str(exc)) from exc
            if not mark_ready(paths["peak_frames"] / "batch-manifest.json", data_path, html_path):
                raise StandardWorkflowError("report", "final report failed manifest validation")
            completed = _summary(
                request,
                paths,
                status="complete",
                diagnostic_data=paths["analysis"] / "diagnostic-analysis-data.json",
                diagnostic_prompt=paths["analysis"] / "diagnostic-analysis-prompt.md",
                report=html_path,
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
