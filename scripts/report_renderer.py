"""Asset handling and HTML rendering for the final report."""

import base64
import json
import mimetypes
import os
from pathlib import Path
import shutil
import uuid


class ReportRenderingError(RuntimeError):
    """Raised when report assets or the HTML template cannot be published."""


REPORT_TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "final-report-template.html"
REPORT_DATA_PLACEHOLDER = "__REPORT_DATA_JSON__"


def _text(value):
    return str(value).strip() if value is not None else ""


def safe_component(value):
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in str(value)
    )
    return cleaned.strip("_") or "unknown-material"


def safe_asset_filename(value):
    name = Path(_text(value)).name
    cleaned = "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in name
    ).strip("._")
    return cleaned or "asset.jpg"


def atomic_write_text(path, content):
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReportRenderingError(
            f"unable to create output directory {target.parent}: {exc}"
        ) from exc
    staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        staging.write_text(content, encoding="utf-8")
        os.replace(staging, target)
    except OSError as exc:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReportRenderingError(f"unable to write report output {target}: {exc}") from exc


def copy_html_asset(source_path, asset_base_dir, material_id):
    target_dir = (
        Path(asset_base_dir) / "assets" / "highlight-frames" / safe_component(material_id)
    )
    target_path = target_dir / safe_asset_filename(source_path.name)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        resolved_source = source_path.resolve()
        resolved_target = target_path.resolve()
        resolved_base = Path(asset_base_dir).resolve()
        if resolved_source != resolved_target:
            shutil.copy2(resolved_source, resolved_target)
        return os.path.relpath(resolved_target, resolved_base)
    except OSError as exc:
        raise ReportRenderingError(
            f"unable to copy report image asset: {source_path}"
        ) from exc


def html_asset_src(path, asset_base_dir, embed_assets=False, copy_assets=False, material_id=""):
    source = _text(path)
    if not source or asset_base_dir is None:
        return source
    if embed_assets and copy_assets:
        raise ReportRenderingError("--embed-assets and --copy-assets are mutually exclusive")
    try:
        source_path = Path(source)
        if not source_path.is_absolute():
            source_path = Path.cwd() / source_path
        source_path = source_path.resolve()
        if embed_assets:
            try:
                payload = source_path.read_bytes()
            except OSError as exc:
                raise ReportRenderingError(
                    f"unable to embed report image asset: {source_path}"
                ) from exc
            mime_type, _encoding = mimetypes.guess_type(str(source_path))
            if not mime_type or not mime_type.startswith("image/"):
                mime_type = "image/jpeg"
            encoded = base64.b64encode(payload).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"
        if copy_assets:
            return Path(copy_html_asset(source_path, asset_base_dir, material_id)).as_posix()
        relative = os.path.relpath(source_path, Path(asset_base_dir).resolve())
    except (OSError, ValueError):
        return source
    return Path(relative).as_posix()


def _load_report_template():
    try:
        template = REPORT_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportRenderingError(
            f"unable to read report template: {REPORT_TEMPLATE_PATH}"
        ) from exc
    if REPORT_DATA_PLACEHOLDER not in template:
        raise ReportRenderingError(
            f"report template is missing {REPORT_DATA_PLACEHOLDER}"
        )
    return template


def json_for_html_script(data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def report_data_for_template(
    data,
    asset_base_dir,
    embed_assets=False,
    copy_assets=False,
    contains_execution_process_term=None,
):
    materials = []
    for material in data.get("materials", []):
        material_id = material.get("materialId")
        highlight_frames = []
        for frame in material.get("highlightFrames", []):
            highlight_frames.append(
                {
                    "rankType": frame.get("rankType"),
                    "durationSec": frame.get("durationSec"),
                    "metricValue": frame.get("metricValue"),
                    "framePath": html_asset_src(
                        frame.get("framePath", ""),
                        asset_base_dir,
                        embed_assets,
                        copy_assets,
                        material_id,
                    ),
                }
            )
        materials.append(
            {
                "rank": material.get("rank"),
                "materialId": material_id,
                "materialName": material.get("materialName"),
                "extractionStatus": material.get("extractionStatus"),
                "highlightFrameCount": material.get("highlightFrameCount"),
                "highlightFrames": highlight_frames,
                "interactionTimeline": material.get("interactionTimeline", {"metrics": []}),
                "scriptAnalysis": material.get(
                    "scriptAnalysis",
                    {"materialUri": "", "formula": [], "labels": [], "evidence": [], "text": ""},
                ),
                "video": material.get("video", {"src": "", "poster": "", "durationSec": None}),
                "analysis": material.get("analysis") or {},
            }
        )
    conclusion = data.get("conclusion", {})
    warnings = [
        _text(warning)
        for warning in data.get("warnings", [])
        if _text(warning)
        and not (
            callable(contains_execution_process_term)
            and contains_execution_process_term(warning)
        )
    ]
    return {
        "schemaVersion": data.get("schemaVersion"),
        "generatedAt": data.get("generatedAt"),
        "status": data.get("status"),
        "period": data.get("period", {}),
        "materials": materials,
        "defaultMaterialId": data.get("defaultMaterialId"),
        "batchInsights": data.get("batchInsights", {}),
        "audience": data.get("audience", {}),
        "scripts": data.get("scripts", {}),
        "conclusion": {
            "overallConclusion": conclusion.get("overallConclusion", ""),
            "nextActions": conclusion.get("nextActions", []),
        },
        "warnings": warnings,
    }


def render_html_report(
    data,
    asset_base_dir=None,
    embed_assets=False,
    copy_assets=False,
    contains_execution_process_term=None,
):
    template_data = report_data_for_template(
        data,
        asset_base_dir,
        embed_assets,
        copy_assets,
        contains_execution_process_term,
    )
    return _load_report_template().replace(
        REPORT_DATA_PLACEHOLDER, json_for_html_script(template_data)
    )
