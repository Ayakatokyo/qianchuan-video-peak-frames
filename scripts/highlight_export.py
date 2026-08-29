"""Build and export Chapter 04 high-light frame assets as an XLSX workbook."""

from collections import OrderedDict
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


CATEGORY_LABELS = {"portrait": "人像画面", "product": "产品画面", "other": "其他画面"}


def _text(value):
    return str(value).strip() if value is not None else ""


def _category_by_ref(presentation):
    result = {}
    chapter = (presentation or {}).get("chapters", {}).get("chapter04", {})
    for category, group in (chapter.get("groups", {}) or {}).items():
        if not isinstance(group, dict):
            continue
        label = CATEGORY_LABELS.get(category, category)
        for ref in group.get("frameRefs", []):
            if _text(ref):
                result[_text(ref)] = label
    return result


def _frame_ref(material_id, frame):
    return _text(frame.get("frameRef")) or f"highlight:{material_id}:{_text(frame.get('rankType'))}"


def build_highlight_export_rows(data, presentation=None):
    """Return user-facing rows for available Chapter 04 frame assets."""
    category_by_ref = _category_by_ref(presentation)
    rows = []
    for material in (data or {}).get("materials", []):
        if not isinstance(material, dict):
            continue
        material_id = _text(material.get("materialId"))
        title = _text(material.get("materialName")) or material_id
        for frame in material.get("highlightFrames", []):
            if not isinstance(frame, dict) or not _text(frame.get("framePath")):
                continue
            rank_type = _text(frame.get("rankType"))
            peak_rank = rank_type.removeprefix("peak_top_") if rank_type.startswith("peak_top_") else "-"
            frame_label = "封面画面" if rank_type == "zero_second" else f"点击 Top{peak_rank} 波峰画面"
            image_path = _text(frame.get("framePath"))
            image_path = str(Path(image_path).expanduser().resolve()) if image_path else ""
            rows.append(OrderedDict([
                ("视觉类型", category_by_ref.get(_frame_ref(material_id, frame), "其他画面")),
                ("素材排名", material.get("rank")),
                ("素材 ID", material_id),
                ("素材标题", title),
                ("画面类型", frame_label),
                ("秒数", frame.get("durationSec")),
                ("点击峰值排名", peak_rank),
                ("点击指数", frame.get("metricValue")),
                ("__image_path", image_path),
            ]))
    return rows


def export_highlight_workbook(data, presentation, output_path):
    rows = build_highlight_export_rows(data, presentation)
    if not rows:
        return None
    node_path = os.environ.get("QIANCHUAN_NODE_PATH") or "/Users/shuzida/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    module_root = Path(os.environ.get("QIANCHUAN_NODE_MODULES", "/Users/shuzida/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"))
    builder = Path(__file__).with_name("build_highlight_workbook.mjs")
    target = Path(output_path).resolve()
    if not Path(node_path).is_file() or not module_root.is_dir() or not builder.is_file():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qianchuan-highlight-xlsx-") as temp_dir:
        temp = Path(temp_dir)
        (temp / "node_modules").symlink_to(module_root, target_is_directory=True)
        input_path = temp / "rows.json"
        input_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        temp_builder = temp / "build_highlight_workbook.mjs"
        temp_builder.write_text(builder.read_text(encoding="utf-8"), encoding="utf-8")
        result = subprocess.run([node_path, str(temp_builder), str(input_path), str(target)], cwd=temp, capture_output=True, text=True)
        if result.returncode != 0 or not target.is_file():
            return None
    return target
