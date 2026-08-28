"""Asset handling and HTML rendering for the final report."""

import base64
from copy import deepcopy
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import uuid
import zipfile


class ReportRenderingError(RuntimeError):
    """Raised when report assets or the HTML template cannot be published."""


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPORT_TEMPLATE_PATH = SKILL_ROOT / "templates" / "final-report-template.html"
REPORT_STYLE_PATH = SKILL_ROOT / "assets" / "report" / "unified-report.css"
REPORT_SCRIPT_PATH = SKILL_ROOT / "assets" / "report" / "unified-report.js"
REPORT_DATA_PLACEHOLDER = "__REPORT_DATA_JSON__"
REPORT_STYLE_PLACEHOLDER = "__REPORT_STYLE_CSS__"
REPORT_SCRIPT_PLACEHOLDER = "__REPORT_SCRIPT_JS__"
REPORT_ARCHIVE_IMAGE_PATTERN = re.compile(
    r"assets/highlight-frames/[^\"'\s<>]+"
)


def _text(value):
    return str(value).strip() if value is not None else ""


PORTFOLIO_SUMMARY_KEYS = (
    "schemaVersion",
    "period",
    "newMaterialCount7d",
    "newMaterialStartDate",
    "newMaterialEndDate",
    "materialCount",
    "summaryMetrics",
)


def _portfolio_summary(value):
    raw = value if isinstance(value, dict) else {}
    if "summaryMetrics" not in raw:
        return deepcopy(raw)
    return {key: deepcopy(raw[key]) for key in PORTFOLIO_SUMMARY_KEYS if key in raw}


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


def _report_image_references(report_dir, html_text):
    root = Path(report_dir).resolve()
    references = set(REPORT_ARCHIVE_IMAGE_PATTERN.findall(html_text))
    for reference in references:
        relative_path = Path(reference)
        if (
            relative_path.is_absolute()
            or relative_path.parts[:2] != ("assets", "highlight-frames")
            or any(part in {"", ".", ".."} for part in relative_path.parts)
        ):
            raise ReportRenderingError(f"unsafe report image reference: {reference}")
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ReportRenderingError(
                f"unsafe report image reference: {reference}"
            ) from exc
        if not candidate.is_file() or candidate.is_symlink():
            raise ReportRenderingError(f"referenced report image is missing: {reference}")
    return references


def _validate_report_archive(archive_path, report_name, references):
    prefix = f"{report_name}/"
    required = {
        f"{prefix}final-report.html",
        f"{prefix}final-report-data.json",
        f"{prefix}presentation-report.json",
        *(f"{prefix}{reference}" for reference in references),
    }
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReportRenderingError("report archive is unreadable") from exc
    if any(not name.startswith(prefix) for name in names) or not required.issubset(names):
        raise ReportRenderingError("report archive content is invalid")


def create_report_archive(report_dir, archive_path=None):
    root = Path(report_dir).resolve()
    html_path = root / "final-report.html"
    data_path = root / "final-report-data.json"
    presentation_path = root / "presentation-report.json"
    if (
        not root.is_dir()
        or not html_path.is_file()
        or not data_path.is_file()
        or not presentation_path.is_file()
    ):
        raise ReportRenderingError("report directory is missing required files")

    target = (
        Path(archive_path) if archive_path is not None
        else root.parent / f"{root.name}.zip"
    )
    try:
        target = target.resolve()
        target.relative_to(root)
    except ValueError:
        pass
    else:
        raise ReportRenderingError("report archive must not be inside the report directory")
    if not target.parent.is_dir():
        raise ReportRenderingError(f"report archive parent is missing: {target.parent}")

    references = _report_image_references(
        root, html_path.read_text(encoding="utf-8")
    )
    staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(root.rglob("*")):
                if source.is_file() and not source.is_symlink():
                    archive.write(source, source.relative_to(root.parent).as_posix())
        _validate_report_archive(staging, root.name, references)
        os.replace(staging, target)
    except (OSError, zipfile.BadZipFile) as exc:
        staging.unlink(missing_ok=True)
        raise ReportRenderingError(f"unable to create report archive: {target}") from exc
    except ReportRenderingError:
        staging.unlink(missing_ok=True)
        raise
    return target


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


def _read_report_source(path, label):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ReportRenderingError(
            f"unable to read {label}: {path}"
        ) from exc


def _load_report_template():
    template = _read_report_source(REPORT_TEMPLATE_PATH, "report template")
    for placeholder in (
        REPORT_DATA_PLACEHOLDER,
        REPORT_STYLE_PLACEHOLDER,
        REPORT_SCRIPT_PLACEHOLDER,
    ):
        if template.count(placeholder) != 1:
            raise ReportRenderingError(
                f"report template must contain exactly one {placeholder}"
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
    def presentation_for_template(presentation):
        if not isinstance(presentation, dict):
            return presentation
        result = deepcopy(presentation)
        chapters = result.get("chapters") if isinstance(result.get("chapters"), dict) else {}
        chapter01 = chapters.get("chapter01") if isinstance(chapters.get("chapter01"), dict) else {}
        if "portfolio" in chapter01:
            chapter01["portfolio"] = _portfolio_summary(chapter01.get("portfolio"))
        chapter04 = chapters.get("chapter04") if isinstance(chapters.get("chapter04"), dict) else {}
        groups = chapter04.get("groups") if isinstance(chapter04.get("groups"), dict) else {}
        for group in groups.values():
            if not isinstance(group, dict):
                continue
            for frame in group.get("frames", []):
                if isinstance(frame, dict):
                    frame["framePath"] = html_asset_src(
                        frame.get("framePath", ""),
                        asset_base_dir,
                        embed_assets,
                        copy_assets,
                        frame.get("materialId", ""),
                    )
        chapter05 = chapters.get("chapter05") if isinstance(chapters.get("chapter05"), dict) else {}
        for material in chapter05.get("materials", []):
            if not isinstance(material, dict):
                continue
            material_id = material.get("materialId", "")
            highlight = material.get("highlight") if isinstance(material.get("highlight"), dict) else {}
            for frame in highlight.get("frames", []):
                if isinstance(frame, dict):
                    frame["framePath"] = html_asset_src(
                        frame.get("framePath", ""),
                        asset_base_dir,
                        embed_assets,
                        copy_assets,
                        material_id,
                    )
        return result

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
                "materialAudience": material.get("materialAudience", {"metrics": {}}),
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
        "presentation": presentation_for_template(data.get("presentation")),
        "generatedAt": data.get("generatedAt"),
        "runId": data.get("runId")
        or (data.get("pipelineSummary") or {}).get("runId"),
        "status": data.get("status"),
        "period": data.get("period", {}),
        "pipelineSummary": data.get("pipelineSummary", {}),
        "portfolio": _portfolio_summary(data.get("portfolio", {})),
        "materials": materials,
        "defaultMaterialId": data.get("defaultMaterialId"),
        "batchInsights": data.get("batchInsights", {}),
        "audience": data.get("audience", {}),
        "scripts": data.get("scripts", {}),
        "conclusion": {
            "schemaVersion": conclusion.get("schemaVersion"),
            "overallConclusion": conclusion.get("overallConclusion", ""),
            "portfolioDimensionConclusions": conclusion.get(
                "portfolioDimensionConclusions", {}
            ),
            "highlightDashboard": conclusion.get("highlightDashboard", {}),
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
    template = _load_report_template()
    style = _read_report_source(REPORT_STYLE_PATH, "report style")
    script = _read_report_source(REPORT_SCRIPT_PATH, "report script")
    return (
        template.replace(REPORT_STYLE_PLACEHOLDER, style)
        .replace(REPORT_SCRIPT_PLACEHOLDER, script)
        .replace(
            '<link rel="stylesheet" href="../assets/report/unified-report.css" data-template-preview-style="true" />\n',
            ""
        )
        .replace(
            '<link\n    rel="stylesheet"\n    href="../assets/report/unified-report.css"\n    data-template-preview-style="true"\n  />\n',
            ""
        )
        .replace(REPORT_DATA_PLACEHOLDER, json_for_html_script(template_data))
    )
