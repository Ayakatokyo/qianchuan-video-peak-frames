"""Pure parsing, selection, and rendering helpers for top materials."""

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


RANKING_METRIC = "stat_cost_for_overall_roi2"
AIGC_MATERIAL_ID = "-2"
AIGC_EXCLUSION_REASON = (
    "AIGC dynamic creative collection cannot map to a single-material diagnosis RPA"
)


class MaterialDataError(ValueError):
    """Raised when origin data cannot produce a deterministic material ranking."""


@dataclass(frozen=True)
class Material:
    material_id: str
    material_name: str
    material_type: str
    video_type: str
    total_cost: Decimal
    video_url: str | None = None
    video_url_candidates: tuple = ()


@dataclass(frozen=True)
class ExcludedMaterial:
    material_id: str
    reason: str
    source_row_count: int
    total_cost: Decimal


@dataclass
class _MaterialAggregate:
    material_id: str
    total_cost: Decimal
    metadata_costs: dict
    video_url_candidates: list

    def add(self, name, material_type, video_type, cost, video_urls=()):
        self.total_cost += cost
        key = (name, material_type, video_type)
        self.metadata_costs[key] = self.metadata_costs.get(key, Decimal("0")) + cost
        for url in video_urls:
            if url not in self.video_url_candidates:
                self.video_url_candidates.append(url)

    def to_material(self):
        name, material_type, video_type = sorted(
            self.metadata_costs.items(), key=lambda item: (-item[1], item[0])
        )[0][0]
        return Material(
            material_id=self.material_id,
            material_name=name,
            material_type=material_type,
            video_type=video_type,
            total_cost=self.total_cost,
            video_url=(self.video_url_candidates[0] if self.video_url_candidates else None),
            video_url_candidates=tuple(self.video_url_candidates),
        )


class MaterialAggregator:
    """Incrementally aggregate material origin rows before final ranking."""

    def __init__(self, name_pattern, material_types, top_n):
        self.pattern = _normalise_name_pattern(name_pattern)
        self.allowed_types = _normalise_material_types(material_types)
        _validate_top_n(top_n)
        self.top_n = top_n
        self.grouped = {}
        self.aigc_count = 0
        self.aigc_cost = Decimal("0")

    def add_raw_origin_data(self, raw_row):
        material_id, name, material_type, video_type, cost, video_urls = _parse_origin_data(raw_row)
        if self.pattern is not None and self.pattern.search(name) is None:
            return
        if self.allowed_types and material_type not in self.allowed_types:
            return

        if material_id == AIGC_MATERIAL_ID:
            self.aigc_count += 1
            self.aigc_cost += cost
            return

        previous = self.grouped.get(material_id)
        if previous is None:
            previous = _MaterialAggregate(
                material_id=material_id,
                total_cost=Decimal("0"),
                metadata_costs={},
                video_url_candidates=[],
            )
            self.grouped[material_id] = previous
        previous.add(name, material_type, video_type, cost, video_urls)

    def select(self):
        materials = sorted(
            (aggregate.to_material() for aggregate in self.grouped.values()),
            key=lambda material: (-material.total_cost, material.material_id),
        )[: self.top_n]
        if not materials:
            raise MaterialDataError("no eligible materials found")

        excluded = []
        if self.aigc_count:
            excluded.append(
                ExcludedMaterial(
                    material_id=AIGC_MATERIAL_ID,
                    reason=AIGC_EXCLUSION_REASON,
                    source_row_count=self.aigc_count,
                    total_cost=self.aigc_cost,
                )
            )
        return materials, excluded


def select_materials(raw_origin_data, name_pattern, material_types, top_n):
    """Parse, filter, aggregate, and rank material-origin JSON rows."""
    aggregator = MaterialAggregator(name_pattern, material_types, top_n)
    for raw_row in raw_origin_data:
        aggregator.add_raw_origin_data(raw_row)
    return aggregator.select()


def validate_material_origin_data(raw_row):
    """Validate a row before it is added to any selection aggregate."""
    _parse_origin_data(raw_row)


def build_output(
    actual_start_date,
    actual_end_date,
    name_pattern,
    material_types,
    advertiser_id,
    materials,
    excluded,
    skipped_source_records=(),
    portfolio_facts=None,
):
    """Build the JSON-serializable output contract for selected materials."""
    pattern = _normalise_name_pattern(name_pattern)
    normalised_types = _normalise_material_types(material_types)
    normalised_advertiser_id = _normalise_advertiser_id(advertiser_id)
    filters = {
        "materialNameRegex": None if pattern is None else pattern.pattern,
        "materialTypes": list(normalised_types),
    }
    if normalised_advertiser_id is not None:
        filters["advertiserId"] = normalised_advertiser_id
    output = {
        "actualStartDate": actual_start_date,
        "actualEndDate": actual_end_date,
        "materialCount": len(materials),
        "rankingMetric": RANKING_METRIC,
        "filters": filters,
        "excluded": [
            {
                "materialId": item.material_id,
                "reason": item.reason,
                "sourceRowCount": item.source_row_count,
                "totalCost": float(item.total_cost),
            }
            for item in excluded
        ],
        "skippedSourceRecordCount": len(skipped_source_records),
        "skippedSourceRecords": list(skipped_source_records),
        "materials": [
            {
                "actualStartDate": actual_start_date,
                "actualEndDate": actual_end_date,
                "rankingMetric": RANKING_METRIC,
                "rank": rank,
                "materialId": material.material_id,
                "materialName": material.material_name,
                "materialType": material.material_type,
                "videoType": material.video_type,
                "totalCost": float(material.total_cost),
                **({
                    "videoUrl": material.video_url,
                    "videoUrlCandidates": list(material.video_url_candidates),
                } if material.video_url_candidates else {}),
            }
            for rank, material in enumerate(materials, start=1)
        ],
    }
    if portfolio_facts is not None:
        output["portfolioFacts"] = portfolio_facts
    return output


def _parse_origin_data(raw_row):
    if not isinstance(raw_row, str):
        raise MaterialDataError("origin_data must be a JSON string")
    try:
        row = json.loads(
            raw_row,
            parse_int=Decimal,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise MaterialDataError("origin_data is not valid JSON") from error
    if not isinstance(row, dict):
        raise MaterialDataError("origin_data must be a JSON object")

    material_id = _normalise_material_id(
        _nested_value(row, "dimensions", "material_id", "Value")
    )
    name = _normalise_string(
        _nested_value(row, "dimensions", "roi2_material_video_name", "ValueStr"),
        "material name",
    )
    material_type = _normalise_string(
        _nested_value(row, "dimensions", "roi2_material_type_v3", "ValueStr"),
        "material type",
    )
    video_type = _normalise_string(
        _nested_value(row, "dimensions", "roi2_material_video_type", "ValueStr"),
        "video type",
    )
    cost = _normalise_cost(
        _nested_value(row, "metrics", "stat_cost_for_overall_roi2", "Value")
    )
    video_urls = _parse_video_url_candidates(
        row.get("dimensions", {}).get("roi2_material_video_play_info")
    )
    return material_id, name, material_type, video_type, cost, video_urls


def _parse_video_url_candidates(field):
    """Extract ordered HTTP(S) URLs from the API play-resource dimension."""
    if not isinstance(field, dict):
        return ()
    values = []
    for key in ("Value", "ValueStr"):
        value = field.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                value = value.strip()
                if value.startswith(("http://", "https://")):
                    values.append({"Url": value})
                continue
        values.append(value)

    urls = []
    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() == "urllist":
                    visit(value)
                elif key.lower() in {"url", "playurl"} and isinstance(value, str):
                    candidate = value.strip()
                    if candidate.startswith(("http://", "https://")) and candidate not in urls:
                        urls.append(candidate)
                else:
                    visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    for value in values:
        visit(value)
    return tuple(urls)


def _nested_value(row, *path):
    current = row
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise MaterialDataError("missing origin_data path: %s" % ".".join(path))
        current = current[key]
    return current


def _normalise_material_id(value):
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise MaterialDataError("material ID must be nonempty")
    material_id = str(value).strip()
    if not material_id:
        raise MaterialDataError("material ID must be nonempty")
    return material_id


def _normalise_string(value, label):
    if not isinstance(value, str):
        raise MaterialDataError("%s must be a nonempty string" % label)
    result = value.strip()
    if not result:
        raise MaterialDataError("%s must be a nonempty string" % label)
    return result


def _normalise_cost(value):
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
        raise MaterialDataError("cost must be a finite non-negative decimal")
    try:
        cost = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise MaterialDataError("cost must be a finite non-negative decimal") from error
    if not cost.is_finite() or cost < 0:
        raise MaterialDataError("cost must be a finite non-negative decimal")
    return cost


def _normalise_name_pattern(name_pattern):
    if name_pattern is None:
        return None
    if isinstance(name_pattern, str):
        try:
            return re.compile(name_pattern)
        except re.error as error:
            raise MaterialDataError("material name regex is invalid") from error
    if callable(getattr(name_pattern, "search", None)) and isinstance(
        getattr(name_pattern, "pattern", None), str
    ):
        return name_pattern
    raise MaterialDataError("material name regex must be compiled or a string")


def _normalise_material_types(material_types):
    if material_types is None:
        return ()
    try:
        types = tuple(material_types)
    except TypeError as error:
        raise MaterialDataError("material types must be iterable") from error
    if any(not isinstance(item, str) for item in types):
        raise MaterialDataError("material types must be strings")
    return types


def _validate_top_n(top_n):
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n <= 0:
        raise MaterialDataError("top_n must be positive")


def _normalise_advertiser_id(advertiser_id):
    if advertiser_id is None:
        return None
    if isinstance(advertiser_id, bool) or not isinstance(advertiser_id, (str, int)):
        raise MaterialDataError("advertiser_id must contain digits only")
    result = str(advertiser_id).strip()
    if not re.fullmatch(r"\d+", result):
        raise MaterialDataError("advertiser_id must contain digits only")
    return result


def _reject_json_constant(value):
    raise ValueError("invalid JSON constant: %s" % value)
