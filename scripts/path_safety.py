"""Validation and safe path construction for externally supplied material IDs."""

import os
import re
from pathlib import Path


_MATERIAL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def validate_material_id(value):
    material_id = str(value or "").strip()
    if _MATERIAL_ID_PATTERN.fullmatch(material_id) is None:
        raise ValueError("materialId must contain only ASCII letters, digits, '_' or '-'")
    return material_id


def _under_root(root, target):
    resolved_root = root.resolve()
    resolved_target = target.resolve()
    try:
        common = os.path.commonpath((str(resolved_root), str(resolved_target)))
    except ValueError as exc:
        raise ValueError("artifact path is on a different filesystem root") from exc
    if common != str(resolved_root):
        raise ValueError("artifact path escapes its root directory")
    return target


def artifact_path(root, material_id, suffix=""):
    root_path = Path(root).absolute()
    safe_id = validate_material_id(material_id)
    return _under_root(root_path, root_path / f"{safe_id}{suffix}")


def material_dir(root, material_id):
    root_path = Path(root).absolute()
    safe_id = validate_material_id(material_id)
    return _under_root(root_path, root_path / safe_id)
