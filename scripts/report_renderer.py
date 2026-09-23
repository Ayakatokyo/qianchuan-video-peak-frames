"""Asset handling and HTML rendering for the final report."""

import base64
from copy import deepcopy
import errno
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import uuid
import zipfile

from portfolio_projection import project_portfolio_summary


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
REPORT_ARCHIVE_VIDEO_PATTERN = re.compile(
    r"assets/videos/[^\"'\s<>]+"
)
ARCHIVE_STORED_SUFFIXES = {
    ".mp4", ".mov", ".webm", ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".xlsx", ".zip", ".gz",
}


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
        unresolved = root / relative_path
        if any(p.is_symlink() for p in (unresolved, *unresolved.parents) if p.is_relative_to(root)):
            raise ReportRenderingError(f"symbolic report resource: {reference}")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ReportRenderingError(
                f"unsafe report image reference: {reference}"
            ) from exc
        if not candidate.is_file() or candidate.is_symlink():
            raise ReportRenderingError(f"referenced report image is missing: {reference}")
    return references


def _report_video_references(report_dir, html_text):
    root = Path(report_dir).resolve()
    references = set(REPORT_ARCHIVE_VIDEO_PATTERN.findall(html_text))
    for reference in references:
        relative_path = Path(reference)
        if (
            relative_path.is_absolute()
            or relative_path.parts[:2] != ("assets", "videos")
            or any(part in {"", ".", ".."} for part in relative_path.parts)
        ):
            raise ReportRenderingError(f"unsafe report video reference: {reference}")
        unresolved = root / relative_path
        if any(p.is_symlink() for p in (unresolved, *unresolved.parents) if p.is_relative_to(root)):
            raise ReportRenderingError(f"symbolic report resource: {reference}")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ReportRenderingError(
                f"unsafe report video reference: {reference}"
            ) from exc
        if not candidate.is_file() or candidate.is_symlink():
            raise ReportRenderingError(f"referenced report video is missing: {reference}")
    return references


def _validate_report_archive(
    archive_path,
    report_dir,
    report_name,
    image_references,
    video_references,
    include_display_facts=False,
):
    prefix = f"{report_name}/"
    required = {
        f"{prefix}final-report.html",
        f"{prefix}final-report-data.json",
        f"{prefix}presentation-report.json",
        *(f"{prefix}{reference}" for reference in image_references),
        *(f"{prefix}{reference}" for reference in video_references),
    }
    if include_display_facts:
        required.add(f"{prefix}report-display-facts.json")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            names = {info.filename for info in infos}
            if len(names) != len(infos):
                raise ReportRenderingError("report archive is corrupt or contains duplicates")
            for info in infos:
                source = Path(report_dir).parent / info.filename
                if not source.is_file() or source.stat().st_size != info.file_size:
                    raise ReportRenderingError("report archive member size is invalid")
                # Stored media is already compressed and can dominate archive size.
                # A completed central directory plus exact source/member size is the
                # bounded validation path; compressed text is still fully CRC-read.
                if info.compress_type != zipfile.ZIP_STORED:
                    with archive.open(info) as member:
                        while member.read(1024 * 1024):
                            pass
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

    image_references = _report_image_references(
        root, html_path.read_text(encoding="utf-8")
    )
    video_references = _report_video_references(
        root, html_path.read_text(encoding="utf-8")
    )
    staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(staging, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(root.rglob("*")):
                if source.name in {
                    "video-resource-cleanup.json",
                    "report-build-receipt.json",
                }:
                    continue  # Receipt binds the immutable ZIP; it cannot be inside it.
                if source.is_symlink():
                    raise ReportRenderingError(f"symbolic report resource: {source}")
                if source.is_file():
                    compression = (
                        zipfile.ZIP_STORED
                        if source.suffix.lower() in ARCHIVE_STORED_SUFFIXES
                        else zipfile.ZIP_DEFLATED
                    )
                    archive.write(
                        source,
                        source.relative_to(root.parent).as_posix(),
                        compress_type=compression,
                    )
        _validate_report_archive(
            staging,
            root,
            root.name,
            image_references,
            video_references,
            include_display_facts=(root / "report-display-facts.json").is_file(),
        )
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


def _copy_and_hash_video_asset(source_path, asset_base_dir, material_id):
    target_dir = Path(asset_base_dir) / "assets" / "videos"
    target_path = target_dir / f"{safe_component(material_id)}.mp4"
    staging = None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        resolved_source = Path(source_path).resolve()
        resolved_target = target_path.resolve()
        digest = hashlib.sha256()
        copied_bytes = 0
        if resolved_source != resolved_target:
            staging = resolved_target.with_name(
                f".{resolved_target.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                os.link(resolved_source, staging)
            except OSError as exc:
                if exc.errno not in {
                    errno.EACCES,
                    errno.EPERM,
                    errno.EXDEV,
                    getattr(errno, "ENOTSUP", errno.EPERM),
                }:
                    raise
                with resolved_source.open("rb") as source, staging.open("xb") as target:
                    while chunk := source.read(8 * 1024 * 1024):
                        target.write(chunk)
                        digest.update(chunk)
                        copied_bytes += len(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                shutil.copystat(resolved_source, staging)
            else:
                with resolved_source.open("rb") as source:
                    while chunk := source.read(8 * 1024 * 1024):
                        digest.update(chunk)
                        copied_bytes += len(chunk)
            os.replace(staging, resolved_target)
        else:
            with resolved_source.open("rb") as source:
                while chunk := source.read(8 * 1024 * 1024):
                    digest.update(chunk)
                    copied_bytes += len(chunk)
        return Path("assets/videos") / target_path.name, copied_bytes, digest.hexdigest()
    except OSError as exc:
        raise ReportRenderingError(
            f"unable to copy report video asset: {source_path}"
        ) from exc
    finally:
        if staging is not None:
            staging.unlink(missing_ok=True)


def copy_video_asset(source_path, asset_base_dir, material_id):
    relative, _copied_bytes, _digest = _copy_and_hash_video_asset(
        source_path, asset_base_dir, material_id
    )
    return relative


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReportRenderingError(f"unable to hash video asset: {path}") from exc
    return digest.hexdigest()


def materialize_report_video_assets(
    data,
    peak_output_dir,
    report_dir,
    *,
    require_local_video_assets=False,
    in_place=False,
):
    """Publish MP4s; production owns its input and can avoid a full deep copy."""
    result = data if in_place else deepcopy(data)
    peak_root = Path(peak_output_dir)
    report_root = Path(report_dir)
    source_root = peak_root / "video-assets"
    published = {}
    manifest_entries = {}
    run_root = peak_root.parent.resolve()
    previous_entries = {}
    previous_manifest_path = peak_root / "report-video-assets.json"
    try:
        previous_manifest = json.loads(
            previous_manifest_path.read_text(encoding="utf-8")
        )
        previous_entries = {
            item.get("materialId"): item
            for item in previous_manifest.get("materials", [])
            if isinstance(item, dict) and _text(item.get("materialId"))
        }
    except (AttributeError, OSError, TypeError, json.JSONDecodeError):
        previous_entries = {}

    def run_relative(path):
        try:
            return Path(os.path.relpath(Path(path).resolve(), run_root)).as_posix()
        except (OSError, ValueError):
            return str(Path(path))

    def reusable_entry(material_id, source, target):
        previous = previous_entries.get(material_id)
        if not isinstance(previous, dict) or not target.is_file() or target.is_symlink():
            return None
        try:
            source_stat = source.stat()
            target_stat = target.stat()
        except OSError:
            return None
        delivery_strategy = previous.get("deliveryStrategy")
        if delivery_strategy not in {"hardlink", "original-copy"}:
            return None
        expected = {
            "sourcePath": run_relative(source),
            "previewPath": run_relative(target),
            "sourceBytes": source_stat.st_size,
            "previewBytes": target_stat.st_size,
            "sourceMtimeNs": source_stat.st_mtime_ns,
            "previewMtimeNs": target_stat.st_mtime_ns,
            "deliveryStrategy": delivery_strategy,
        }
        if any(previous.get(key) != value for key, value in expected.items()):
            return None
        source_hash = previous.get("sourceSha256")
        if (
            not isinstance(source_hash, str)
            or len(source_hash) != 64
            or previous.get("previewSha256") != source_hash
            or source_stat.st_size != target_stat.st_size
        ):
            return None
        if delivery_strategy == "hardlink" and not os.path.samefile(source, target):
            return None
        return dict(previous)

    def visit(value):
        if isinstance(value, dict):
            material_id = _text(value.get("materialId"))
            video = value.get("video")
            if material_id and isinstance(video, dict):
                source = source_root / f"{safe_component(material_id)}.mp4"
                if source.is_file() and not source.is_symlink():
                    if material_id not in published:
                        relative_target = Path("assets/videos") / f"{safe_component(material_id)}.mp4"
                        target = report_root / relative_target
                        retained = reusable_entry(material_id, source, target)
                        if retained is None:
                            relative, copied_bytes, source_hash = _copy_and_hash_video_asset(
                                source, report_root, material_id
                            )
                            published[material_id] = relative.as_posix()
                            target = report_root / published[material_id]
                            source_stat = source.stat()
                            target_stat = target.stat()
                            if copied_bytes != source_stat.st_size:
                                raise ReportRenderingError(
                                    f"copied report video size changed: {source}"
                                )
                            retained = {
                                "materialId": material_id,
                                "sourcePath": run_relative(source),
                                "previewPath": run_relative(target),
                                "sourceBytes": source_stat.st_size,
                                "previewBytes": target_stat.st_size,
                                "sourceSha256": source_hash,
                                "previewSha256": source_hash,
                                "sourceMtimeNs": source_stat.st_mtime_ns,
                                "previewMtimeNs": target_stat.st_mtime_ns,
                                "deliveryStrategy": (
                                    "hardlink"
                                    if os.path.samefile(source, target)
                                    else "original-copy"
                                ),
                            }
                        else:
                            published[material_id] = relative_target.as_posix()
                        manifest_entries[material_id] = retained
                    video["src"] = published[material_id]
                elif require_local_video_assets:
                    raise ReportRenderingError(
                        f"original video is missing for material {material_id}: {source}"
                    )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    atomic_write_text(
        peak_root / "report-video-assets.json",
        json.dumps(
            {"schemaVersion": 2, "materials": list(manifest_entries.values())},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return result


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
    return payload.translate(
        str.maketrans(
            {
                "&": "\\u0026",
                "<": "\\u003c",
                ">": "\\u003e",
                "\u2028": "\\u2028",
                "\u2029": "\\u2029",
            }
        )
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
            chapter01["portfolio"] = project_portfolio_summary(chapter01.get("portfolio"))
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
    template_data_presentation = presentation_for_template(data.get("presentation"))
    source_display_facts = data.get("displayFacts", {})
    presentation_chapters = (
        template_data_presentation.get("chapters", {})
        if isinstance(template_data_presentation, dict)
        else {}
    )
    chapter02 = presentation_chapters.get("chapter02")
    chapter05 = presentation_chapters.get("chapter05")
    has_v1_presentation = (
        isinstance(template_data_presentation, dict)
        and template_data_presentation.get("presentationSchemaVersion") == 1
        and (
            (isinstance(chapter05, dict) and chapter05.get("availability") in {"available", "partial"})
            or (isinstance(chapter02, dict) and chapter02.get("availability") in {"available", "partial"})
        )
    )
    # In the v1 browser payload, Chapter 05 Presentation is the single
    # material-detail source. Keep only catalog fields at the legacy top level
    # so highlight/audience/script facts are not embedded twice.
    template_materials = materials
    if has_v1_presentation:
        template_materials = [
            {
                "rank": material.get("rank"),
                "materialId": material.get("materialId"),
                "materialName": material.get("materialName"),
            }
            for material in materials
        ]
    if isinstance(source_display_facts, dict) and has_v1_presentation:
        if isinstance(chapter02, dict):
            chapter02.pop("metrics", None)
            chapter02.pop("preferencesByMetric", None)
        # Chapter 05 Presentation is the sole browser source for per-material
        # facts; keep only the audience drill-down fallback in displayFacts.
        # Chapter 05 Presentation and Chapter 02 Presentation are the only
        # browser consumers of these facts. Keep the audience drill-down
        # fallback only; portfolio, materials, interaction, commercial and
        # ROI snapshots duplicate Presentation/top-level summaries and are
        # not read by the template.
        template_display_facts = {
            "audience": deepcopy(source_display_facts.get("audience", {}))
        }
    else:
        template_display_facts = source_display_facts
        if isinstance(template_display_facts, dict):
            template_display_facts = deepcopy(template_display_facts)
            template_display_facts.pop("highlights", None)
            template_display_facts.pop("delivery", None)
            template_display_facts["materials"] = [
                {
                    key: deepcopy(value)
                    for key, value in material.items()
                    if key not in {"summaryPath", "warnings", "analysis", "highlightFrames"}
                }
                for material in template_display_facts.get("materials", [])
                if isinstance(material, dict)
            ]
    return {
        "schemaVersion": data.get("schemaVersion"),
        "presentation": template_data_presentation,
        "analysisQuality": deepcopy(data.get("analysisQuality", {})),
        "generatedAt": data.get("generatedAt"),
        "runId": data.get("runId")
        or (data.get("pipelineSummary") or {}).get("runId"),
        "status": data.get("status"),
        "period": data.get("period", {}),
        "pipelineSummary": data.get("pipelineSummary", {}),
        "portfolio": project_portfolio_summary(data.get("portfolio", {})),
        "displayFacts": template_display_facts,
        "highlightExportHref": _text(data.get("highlightExportHref")),
        "materials": template_materials,
        "defaultMaterialId": data.get("defaultMaterialId"),
        **({} if has_v1_presentation else {
            "batchInsights": data.get("batchInsights", {}),
            "audience": data.get("audience", {}),
        }),
        **({} if has_v1_presentation else {"scripts": data.get("scripts", {})}),
        "conclusion": {
            "schemaVersion": conclusion.get("schemaVersion"),
            "overallConclusion": conclusion.get("overallConclusion", ""),
            "portfolioDimensionConclusions": conclusion.get(
                "portfolioDimensionConclusions", {}
            ),
            "highlightDashboard": conclusion.get("highlightDashboard", {}),
            **({"customClassification": deepcopy(conclusion["customClassification"])}
               if isinstance(conclusion.get("customClassification"), dict) else {}),
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
