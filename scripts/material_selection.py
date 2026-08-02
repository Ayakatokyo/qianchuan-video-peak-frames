"""Candidate-pool validation and account selection for the CLI."""

import json
from pathlib import Path


def normalize_account_rows(payload, error_type):
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise error_type("RPA authorization list must contain a data array")
    accounts = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        shop_id = str(row.get("shop_id") or "").strip()
        account = str(row.get("account") or "").strip()
        if shop_id:
            accounts.append({"account": account, "shop_id": shop_id})
    return accounts


def format_account_rows(accounts):
    return "\n".join(
        f"{item['account'] or '<unknown-account>'}\t{item['shop_id']}"
        for item in accounts
    )


def load_top_materials(
    path,
    date_range_type,
    custom_start_date,
    custom_end_date,
    *,
    error_type,
    validate_id,
):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise error_type(f"unable to read top materials JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise error_type("top materials JSON must be an object")
    actual_start_date = payload.get("actualStartDate")
    actual_end_date = payload.get("actualEndDate")
    if not isinstance(actual_start_date, str) or not isinstance(actual_end_date, str):
        raise error_type(
            "top materials JSON requires actualStartDate and actualEndDate"
        )
    if date_range_type == "CUSTOM" and (
        actual_start_date != custom_start_date or actual_end_date != custom_end_date
    ):
        raise error_type(
            "top materials JSON date range does not match the requested CUSTOM range"
        )
    materials = payload.get("materials")
    if not isinstance(materials, list) or not materials:
        raise error_type(
            "top materials JSON must contain a nonempty materials list"
        )

    material_ids = []
    for material in materials:
        if not isinstance(material, dict):
            raise error_type("top materials entries must be objects")
        material_id = str(material.get("materialId") or "").strip()
        if not material_id:
            raise error_type("top materials entries require materialId")
        try:
            validate_id(material_id)
        except ValueError as exc:
            raise error_type(
                f"invalid top materials materialId {material_id!r}"
            ) from exc
        if material_id in material_ids:
            raise error_type("top materials entries must not repeat materialId")
        material_ids.append(material_id)
    if payload.get("materialCount") != len(material_ids):
        raise error_type("top materials JSON materialCount does not match materials")
    return payload, material_ids
