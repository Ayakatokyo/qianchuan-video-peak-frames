from datetime import datetime, timezone
import html
import json
import math
import os
from pathlib import Path
import uuid


class CommercialReportError(RuntimeError):
    """Raised when commercial report inputs cannot produce a report."""


def _as_number(value, field_name):
    if isinstance(value, bool):
        raise CommercialReportError(f"{field_name} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CommercialReportError(
            f"{field_name} must be a finite number"
        ) from exc
    if not math.isfinite(number):
        raise CommercialReportError(f"{field_name} must be a finite number")
    return number


def _optional_number(value, default=0.0):
    try:
        return _as_number(value, "number")
    except CommercialReportError:
        return default


def _text(value):
    return str(value).strip() if value is not None else ""


def _safe_component(value):
    text = str(value)
    cleaned = "".join(
        character
        if character.isalnum() or character in "-_"
        else "_"
        for character in text
    )
    return cleaned.strip("_") or "unknown-material"


def _nested_dimension_value(record, dimension_key):
    dimensions = record.get("Dimensions")
    if not isinstance(dimensions, dict):
        return ""

    candidates = []
    if dimension_key in dimensions:
        candidates.append(dimensions[dimension_key])
    if len(dimensions) == 1:
        candidates.extend(dimensions.values())

    for candidate in candidates:
        if isinstance(candidate, dict):
            for key in (
                "ValueStr",
                "valueStr",
                "Value",
                "value",
                "Name",
                "name",
                "label",
            ):
                value = _text(candidate.get(key))
                if value:
                    return value
        else:
            value = _text(candidate)
            if value:
                return value
    return ""


def _bucket_label(record, dimension_key):
    for key in ("dimensionValue", "dimensionValueName", "bucket", "bucketName"):
        value = _text(record.get(key))
        if value:
            return value
    return _nested_dimension_value(record, dimension_key)


def _is_matching_material(record, material_id):
    record_material_id = _text(record.get("materialId"))
    return not record_material_id or record_material_id == str(material_id)


def aggregate_crowd_analysis(records_by_material, selected_material_ids):
    """Aggregate material-level SHOW_COUNT crowd records by dimension bucket."""
    selected_ids = [str(material_id) for material_id in selected_material_ids]
    dimensions = {}
    missing_material_ids = []

    for material_id in selected_ids:
        material_records = records_by_material.get(material_id, [])
        material_has_valid_crowd = False
        if not isinstance(material_records, list):
            material_records = []

        for record in material_records:
            if not isinstance(record, dict):
                continue
            if record.get("recordType") != "crowd_analysis":
                continue
            if record.get("displayMetric") != "SHOW_COUNT":
                continue
            if not _is_matching_material(record, material_id):
                continue

            dimension_key = _text(record.get("dimensionKey"))
            if not dimension_key:
                continue
            bucket = _bucket_label(record, dimension_key)
            if not bucket:
                continue
            metric_value = _optional_number(record.get("metricValue"), default=None)
            if metric_value is None or metric_value < 0:
                continue

            material_has_valid_crowd = True
            dimension = dimensions.setdefault(
                dimension_key,
                {
                    "dimensionKey": dimension_key,
                    "dimensionName": _text(record.get("dimensionName"))
                    or dimension_key,
                    "sourceMaterialIds": set(),
                    "_buckets": {},
                },
            )
            dimension["sourceMaterialIds"].add(material_id)
            dimension["_buckets"][bucket] = (
                dimension["_buckets"].get(bucket, 0.0) + metric_value
            )

        if not material_has_valid_crowd:
            missing_material_ids.append(material_id)

    normalized_dimensions = {}
    for dimension_key, dimension in dimensions.items():
        bucket_values = dimension["_buckets"]
        total = sum(bucket_values.values())
        buckets = []
        for bucket, metric_value in sorted(
            bucket_values.items(),
            key=lambda item: (-item[1], tuple(-ord(char) for char in item[0])),
        ):
            percentage = (metric_value / total * 100.0) if total else 0.0
            buckets.append(
                {
                    "bucket": bucket,
                    "metricValue": round(metric_value, 6),
                    "metricPercentage": round(percentage, 6),
                }
            )
        normalized_dimensions[dimension_key] = {
            "dimensionKey": dimension_key,
            "dimensionName": dimension["dimensionName"],
            "totalMetricValue": round(total, 6),
            "sourceMaterialIds": sorted(dimension["sourceMaterialIds"]),
            "buckets": buckets,
        }

    return {
        "metric": "SHOW_COUNT",
        "dimensions": normalized_dimensions,
        "missingCrowdMaterialIds": missing_material_ids,
    }


def _read_json(path, purpose):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommercialReportError(f"unable to read {purpose}: {path}") from exc


def _load_top_materials(path):
    payload = _read_json(path, "top materials JSON")
    materials = payload.get("materials") if isinstance(payload, dict) else None
    if not isinstance(materials, list) or not materials:
        raise CommercialReportError(
            "top materials JSON must contain a nonempty materials list"
        )
    return payload, [material for material in materials if isinstance(material, dict)]


def _load_records(records_dir, material_id, warnings):
    path = Path(records_dir) / f"{material_id}.json"
    if not path.is_file():
        warnings.append(f"missing diagnosis records for material {material_id}")
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        warnings.append(
            f"unable to read diagnosis records for material {material_id}: {exc}"
        )
        return []
    if not isinstance(payload, list):
        warnings.append(f"diagnosis records for material {material_id} are not a list")
        return []
    return payload


def _load_summary(peak_output_dir, material_id, warnings):
    result_dir = Path(peak_output_dir) / _safe_component(material_id)
    summary_path = result_dir / "summary.json"
    if not summary_path.is_file():
        warnings.append(f"missing high-light summary for material {material_id}")
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        warnings.append(
            f"unable to read high-light summary for material {material_id}: {exc}"
        )
        return None
    if not isinstance(summary, dict):
        warnings.append(f"high-light summary for material {material_id} is not an object")
        return None

    frames = []
    for frame in summary.get("frames", []):
        if not isinstance(frame, dict):
            continue
        frame_name = _text(frame.get("framePath"))
        frame_path = str(result_dir / frame_name) if frame_name else ""
        frames.append(
            {
                "rankType": frame.get("rankType"),
                "durationSec": frame.get("durationSec"),
                "metricValue": frame.get("metricValue"),
                "framePath": frame_path,
            }
        )
    return {
        "summaryPath": str(summary_path),
        "frames": frames,
        "warnings": summary.get("warnings", []),
    }


def _build_material_analysis(materials, peak_output_dir, records_dir, warnings):
    records_by_material = {}
    analyzed_materials = []
    for material in materials:
        material_id = _text(material.get("materialId"))
        if not material_id:
            warnings.append("top materials entry missing materialId")
            continue

        records_by_material[material_id] = _load_records(
            records_dir, material_id, warnings
        )
        summary = _load_summary(peak_output_dir, material_id, warnings)
        frames = summary["frames"] if summary else []
        analyzed_materials.append(
            {
                "rank": material.get("rank"),
                "materialId": material_id,
                "materialName": material.get("materialName", ""),
                "materialType": material.get("materialType", ""),
                "videoType": material.get("videoType", ""),
                "totalCost": round(_optional_number(material.get("totalCost")), 6),
                "highlightFrameCount": len(frames),
                "highlightFrames": frames,
                "summaryPath": summary["summaryPath"] if summary else "",
            }
        )
    return analyzed_materials, records_by_material


def _atomic_write_text(path, content):
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CommercialReportError(
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
        raise CommercialReportError(f"unable to write report output {target}: {exc}") from exc


def _format_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "—"
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    if number == int(number):
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _render_bucket_bars(dimension):
    rows = []
    for bucket in dimension.get("buckets", []):
        width = min(100.0, max(0.0, float(bucket.get("metricPercentage", 0.0))))
        rows.append(
            "<div class=\"bar-row\">"
            f"<div class=\"bar-meta\"><span>{html.escape(bucket['bucket'])}</span>"
            f"<strong>{_format_number(bucket['metricPercentage'])}%</strong></div>"
            "<div class=\"bar-track\">"
            f"<div class=\"bar-fill\" style=\"width: {width:.2f}%\"></div>"
            "</div>"
            f"<small>曝光量 {_format_number(bucket.get('metricValue'))}</small>"
            "</div>"
        )
    return "\n".join(rows) or "<p class=\"muted\">暂无可用人群桶数据</p>"


def _render_html_report(analysis):
    materials_html = []
    for material in analysis["materials"]:
        frames = []
        for frame in material.get("highlightFrames", []):
            frame_path = frame.get("framePath", "")
            if frame_path:
                frames.append(
                    "<figure>"
                    f"<img src=\"{html.escape(frame_path)}\" alt=\"高光帧\" />"
                    f"<figcaption>{html.escape(str(frame.get('rankType', '')))} · "
                    f"{_format_number(frame.get('durationSec'))}s · "
                    f"点击 {_format_number(frame.get('metricValue'))}</figcaption>"
                    "</figure>"
                )
        if not frames:
            frames.append("<p class=\"muted\">暂无高光帧摘要</p>")

        materials_html.append(
            "<article class=\"material-card\">"
            f"<div class=\"rank\">#{html.escape(str(material.get('rank', '—')))}</div>"
            "<div>"
            f"<h3>{html.escape(str(material.get('materialName', '')))}</h3>"
            f"<p class=\"muted\">ID {html.escape(material['materialId'])} · "
            f"{html.escape(str(material.get('materialType', '')))} · "
            f"{html.escape(str(material.get('videoType', '')))}</p>"
            f"<p class=\"cost\">消耗 ¥{_format_number(material.get('totalCost'))}</p>"
            f"<div class=\"frames\">{''.join(frames)}</div>"
            "</div>"
            "</article>"
        )

    dimensions_html = []
    for dimension in analysis["audience"]["dimensions"].values():
        dimensions_html.append(
            "<section class=\"audience-card\">"
            f"<h3>{html.escape(dimension.get('dimensionName') or dimension['dimensionKey'])}</h3>"
            f"<p class=\"muted\">总曝光量 {_format_number(dimension.get('totalMetricValue'))} · "
            f"覆盖素材 {len(dimension.get('sourceMaterialIds', []))} 个</p>"
            f"{_render_bucket_bars(dimension)}"
            "</section>"
        )

    warnings = analysis.get("warnings", [])
    warnings_html = (
        "".join(f"<li>{html.escape(warning)}</li>" for warning in warnings)
        if warnings
        else "<li>无阻断告警</li>"
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>千川素材商业分析报告</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5efe7;
      --panel: rgba(255, 255, 255, 0.86);
      --ink: #221714;
      --muted: #7d6b60;
      --brand: #9b4d2e;
      --accent: #e6a65d;
      --line: rgba(88, 52, 31, 0.14);
      --shadow: 0 24px 70px rgba(70, 38, 20, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(230, 166, 93, .35), transparent 36rem),
        linear-gradient(135deg, #fff8ef, var(--bg));
    }}
    main {{ width: min(1180px, calc(100% - 40px)); margin: 0 auto; padding: 44px 0 56px; }}
    .hero {{
      padding: 34px;
      border: 1px solid var(--line);
      border-radius: 30px;
      background: linear-gradient(135deg, rgba(255,255,255,.92), rgba(255,246,232,.76));
      box-shadow: var(--shadow);
    }}
    h1 {{ margin: 0 0 12px; font-size: clamp(32px, 5vw, 56px); letter-spacing: -0.04em; }}
    h2 {{ margin: 38px 0 16px; font-size: 26px; }}
    h3 {{ margin: 0 0 8px; }}
    .muted {{ color: var(--muted); }}
    .cards {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-top: 22px; }}
    .metric {{ padding: 20px; border-radius: 22px; background: var(--panel); border: 1px solid var(--line); }}
    .metric span {{ display: block; color: var(--muted); font-size: 13px; }}
    .metric strong {{ display: block; margin-top: 8px; font-size: 30px; }}
    .audience-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 18px; }}
    .audience-card, .material-card, .note {{
      border: 1px solid var(--line);
      border-radius: 24px;
      background: var(--panel);
      box-shadow: 0 16px 44px rgba(70, 38, 20, .08);
      padding: 22px;
    }}
    .bar-row {{ margin: 16px 0; }}
    .bar-meta {{ display: flex; justify-content: space-between; gap: 16px; margin-bottom: 8px; }}
    .bar-track {{ height: 12px; border-radius: 999px; background: rgba(155, 77, 46, .12); overflow: hidden; }}
    .bar-fill {{ height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--brand), var(--accent)); }}
    .material-card {{ display: grid; grid-template-columns: 62px 1fr; gap: 16px; margin-bottom: 18px; }}
    .rank {{ width: 50px; height: 50px; border-radius: 16px; display: grid; place-items: center; background: #2c1811; color: #fff; font-weight: 800; }}
    .cost {{ color: var(--brand); font-size: 20px; font-weight: 800; }}
    .frames {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; }}
    figure {{ margin: 0; border-radius: 18px; overflow: hidden; background: #fff; border: 1px solid var(--line); }}
    img {{ display: block; width: 100%; height: 140px; object-fit: cover; background: #f0e3d5; }}
    figcaption {{ padding: 10px 12px; color: var(--muted); font-size: 13px; }}
    ul {{ margin: 8px 0 0; padding-left: 20px; }}
    @media (max-width: 760px) {{ .cards {{ grid-template-columns: 1fr; }} .material-card {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <p class="muted">统计周期 {html.escape(str(analysis['period'].get('actualStartDate', '')))} — {html.escape(str(analysis['period'].get('actualEndDate', '')))}</p>
      <h1>千川素材商业分析报告</h1>
      <p>基于 TOP 素材池、单素材诊断 RPA 与点击高光截帧，先聚合素材级曝光人群结构，用于快速判断“什么素材触达了什么人”。</p>
      <div class="cards">
        <div class="metric"><span>入选素材</span><strong>{analysis['overview']['materialCount']}</strong></div>
        <div class="metric"><span>整体消耗</span><strong>¥{_format_number(analysis['overview']['totalCost'])}</strong></div>
        <div class="metric"><span>高光帧</span><strong>{analysis['overview']['highlightFrameCount']}</strong></div>
      </div>
    </section>

    <h2>素材级曝光人群结构</h2>
    <div class="audience-grid">
      {''.join(dimensions_html) or '<p class="muted">暂无可聚合人群数据</p>'}
    </div>

    <h2>TOP 素材与高光帧</h2>
    {''.join(materials_html)}

    <h2>方法说明与告警</h2>
    <section class="note">
      <p>人群数据是素材级相关性，不是单帧因果归因；报告中的高光帧用于辅助画面复盘，人群结构来自同一素材的曝光人群记录。</p>
      <ul>{warnings_html}</ul>
    </section>
  </main>
</body>
</html>
"""


def generate_report(
    top_materials_json,
    peak_output_dir,
    diagnosis_records_dir,
    output_json,
    output_html,
):
    top_payload, top_materials = _load_top_materials(top_materials_json)
    warnings = []
    material_ids = [_text(material.get("materialId")) for material in top_materials]
    material_ids = [material_id for material_id in material_ids if material_id]

    materials, records_by_material = _build_material_analysis(
        top_materials, peak_output_dir, diagnosis_records_dir, warnings
    )
    if not materials:
        raise CommercialReportError("no valid top materials available for report")

    audience = aggregate_crowd_analysis(records_by_material, material_ids)
    for material_id in audience["missingCrowdMaterialIds"]:
        warnings.append(f"missing SHOW_COUNT crowd analysis for material {material_id}")

    highlight_frame_count = sum(
        material["highlightFrameCount"] for material in materials
    )
    total_cost = round(
        sum(_optional_number(material.get("totalCost")) for material in materials),
        6,
    )
    analysis = {
        "reportTitle": "千川素材商业分析报告",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "period": {
            "actualStartDate": top_payload.get("actualStartDate", ""),
            "actualEndDate": top_payload.get("actualEndDate", ""),
        },
        "overview": {
            "materialCount": len(materials),
            "rankingMetric": top_payload.get("rankingMetric", "totalCost"),
            "totalCost": total_cost,
            "highlightFrameCount": highlight_frame_count,
        },
        "materials": materials,
        "audience": audience,
        "warnings": warnings,
        "methodology": [
            "Only crowd_analysis records with displayMetric=SHOW_COUNT are aggregated.",
            "Bucket percentages are recalculated from summed raw metricValue values.",
            "Audience data is material-level correlation, not frame-level causality.",
        ],
    }

    _atomic_write_text(
        output_json,
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write_text(output_html, _render_html_report(analysis))
    return analysis
