"""Bounded, lossless views of frozen facts for the current model; no inference.

Tables factor repeated keys and constant columns. Pages contain ordered path/value
records, never a sampled summary. Original units remain the report's source.
"""
import hashlib
import json
from pathlib import Path

from analysis_handoff import inspect_inputs, read_object
from analysis_units_lib import UNIT_CONTRACTS, UNIT_DEFINITIONS

PAGE_BYTES = 12000
FORMAT = "lossless-tables-v1"


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _size(value):
    return len(json_text(value).encode("utf-8"))


def _leaves(value, path=()):
    if isinstance(value, dict) and value:
        return [leaf for key, item in value.items() for leaf in _leaves(item, (*path, key))]
    return [(path, value)]


def _put(root, path, value):
    if not path:
        return value
    parent = root
    for key in path[:-1]:
        if isinstance(parent, dict):
            parent = parent.setdefault(key, {})
        else:
            parent = parent[key]
    parent[path[-1]] = value
    return root


def encode_tables(value):
    """Keep every value, type and order; use a table only when it is smaller."""
    if isinstance(value, dict) and any(k in value for k in ("$table", "$literal")):
        return {"$literal": value}
    items = list(value.values()) if isinstance(value, dict) else value
    if isinstance(items, list) and len(items) >= 3 and all(isinstance(x, dict) and x for x in items):
        flattened = [_leaves(item) for item in items]
        columns = [p for p, _ in flattened[0]]
        # Keep metric/dimension containers named: putting whole bucket arrays in
        # cells hides their business context across pages and costs model effort.
        small_cells = all(not isinstance(v, (list, dict)) or _size(v) <= 128
                          for row in flattened for _, v in row)
        if small_cells and all([p for p, _ in row] == columns for row in flattened):
            rows = [[v for _, v in row] for row in flattened]
            common = {str(i): encode_tables(rows[0][i]) for i in range(len(columns))
                      if all(json_text(row[i]) == json_text(rows[0][i]) for row in rows)}
            table = {"kind": "object" if isinstance(value, dict) else "array"}
            if isinstance(value, dict):
                table["keys"] = list(value)
            table.update(columns=[list(p) for p in columns], common=common,
                         rows=[[encode_tables(v) for i, v in enumerate(row) if str(i) not in common]
                               for row in rows])
            encoded = {"$table": table}
            if _size(encoded) < _size(value):
                return encoded
    if isinstance(value, dict):
        return {k: encode_tables(v) for k, v in value.items()}
    if isinstance(value, list):
        return [encode_tables(v) for v in value]
    return value


def decode_tables(value):
    """Restore a table view for offline equivalence checks."""
    if isinstance(value, dict):
        if set(value) == {"$literal"}:
            return value["$literal"]
        if set(value) == {"$table"}:
            table = value["$table"]
            restored = []
            for row in table["rows"]:
                cells, item = iter(row), {}
                for index, path in enumerate(table["columns"]):
                    cell = table["common"][str(index)] if str(index) in table["common"] else next(cells)
                    _put(item, path, decode_tables(cell))
                restored.append(item)
            return dict(zip(table["keys"], restored)) if table["kind"] == "object" else restored
        return {k: decode_tables(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_tables(v) for v in value]
    return value


def _records(value, path=()):
    record = {"path": list(path), "value": value}
    if _size(record) <= PAGE_BYTES // 2:
        yield record
    elif isinstance(value, dict):
        yield {"path": list(path), "container": "object"}
        for key, item in value.items():
            yield from _records(item, (*path, key))
    elif isinstance(value, list):
        yield {"path": list(path), "container": "array", "length": len(value)}
        # Batch small adjacent entries; recurse only for an oversized entry.
        pending, start, size = [], 0, 0
        overhead = _size({"path": list(path), "start": len(value), "items": []})
        for index, item in enumerate(value):
            item_size = _size(item) + 1
            if pending and overhead + size + item_size > PAGE_BYTES // 2:
                yield {"path": list(path), "start": start, "items": pending}
                pending, size = [], 0
            if item_size + overhead > PAGE_BYTES // 2:
                yield from _records(item, (*path, index))
            else:
                if not pending:
                    start = index
                pending.append(item)
                size += item_size
        if pending:
            yield {"path": list(path), "start": start, "items": pending}
    elif isinstance(value, str):
        yield {"path": list(path), "container": "string"}
        start = 0
        while start < len(value):
            end = min(len(value), start + 1000)
            chunk = {"path": list(path), "start": start, "text": value[start:end]}
            while _size(chunk) > PAGE_BYTES // 2 and end > start + 1:
                end = start + (end - start) // 2
                chunk["text"] = value[start:end]
            yield chunk
            start = end
    else:
        raise ValueError("fact path/value exceeds page budget")


def paginate(value):
    pages, page, size = [], [], 2
    for record in _records(value):
        record_size = _size(record) + 1
        if record_size + 2 > PAGE_BYTES:
            raise ValueError("fact path exceeds page budget")
        if page and size + record_size > PAGE_BYTES:
            pages.append(page)
            page, size = [], 2
        page.append(record)
        size += record_size
    if page:
        pages.append(page)
    return pages


def restore_pages(pages):
    """Reconstruct all ordered records; used by offline real-input verification."""
    root = None
    for page in pages:
        for record in page:
            path = record["path"]
            if "value" in record:
                root = _put(root, path, record["value"])
            elif "container" in record:
                value = {"object": {}, "array": [None] * record.get("length", 0), "string": ""}[record["container"]]
                root = _put(root, path, value)
            else:
                value = root
                for key in path:
                    value = value[key]
                if "items" in record:
                    start = record["start"]
                    value[start:start + len(record["items"])] = record["items"]
                else:
                    if len(value) != record["start"]:
                        raise ValueError("non-contiguous text pages")
                    root = _put(root, path, value + record["text"])
    return root


def _snapshot(root):
    root = Path(root)
    units = {}
    inputs = inspect_inputs(root, loaded_units=units)
    reader_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    binding = hashlib.sha256(json_text({"readerSha256": reader_hash, "inputs": inputs}).encode()).hexdigest()
    manifest = read_object(root / "analysis-units/analysis-unit-manifest.json")
    if hashlib.sha256((root / "analysis-units/analysis-unit-manifest.json").read_bytes()).hexdigest() != inputs["manifestSha256"]:
        raise ValueError("analysis inputs changed while reading")
    return inputs, binding, units


def _tasks(units, material_ids):
    tasks = []
    for unit_id, filename in UNIT_DEFINITIONS:
        if unit_id == "chapter05-material":
            materials = units[unit_id]["materials"]
            if [m["materialId"] for m in materials] != material_ids:
                raise ValueError("material input order does not match manifest")
            for index, material in enumerate(materials):
                tasks.append({"taskId": f"material-{index + 1:03d}", "sourceFile": filename,
                              "sourcePath": ["materials", index], "unitId": unit_id,
                              "materialId": material["materialId"], "outputFields": ["materialAnalyses"]})
        else:
            tasks.append({"taskId": unit_id.split('-')[0], "sourceFile": filename,
                          "sourcePath": [], "unitId": unit_id,
                          "outputFields": (["highlightDashboard", "customClassification"]
                                           if unit_id == "chapter04-highlight"
                                           else ["analysisProvenance", *UNIT_CONTRACTS[unit_id]["outputFields"]]
                                           if unit_id == "chapter01-portfolio"
                                           else UNIT_CONTRACTS[unit_id]["outputFields"])})
    # Per-material work precedes the visual synthesis. Each frame is written once.
    return [t for t in tasks if t["taskId"] != "chapter04"] + [t for t in tasks if t["taskId"] == "chapter04"]


def _task_pages(task, units):
    value = units[task["unitId"]]
    for key in task["sourcePath"]:
        value = value[key]
    return value, paginate(encode_tables(value))


def analysis_plan(analysis_dir, *, verify=False):
    inputs, binding, units = _snapshot(analysis_dir)
    tasks = _tasks(units, inputs["materialIds"])
    for task in tasks:
        value, pages = _task_pages(task, units)
        task.update(pageCount=len(pages), sourceBytes=_size(value),
                    pagedBytes=sum(_size(page) for page in pages))
        if verify:
            if json_text(decode_tables(restore_pages(pages))) != json_text(value):
                raise ValueError(f"lossless round-trip failed: {task['taskId']}")
            task["roundTripVerified"] = True
    # The plan carries the small Chapter 05 envelope so no fact is omitted.
    envelope = {k: v for k, v in units["chapter05-material"].items() if k != "materials"}
    return {"format": FORMAT, "inputSha256": binding, "manifestSha256": inputs["manifestSha256"],
            "promptSha256": inputs["promptSha256"], "period": inputs["period"],
            "materialIds": inputs["materialIds"], "uniqueImageCount": inputs["uniqueImageCount"],
            "frameCount": len(inputs["frames"]), "contentReviewed": False,
            "chapter05Envelope": envelope,
            "sourceBytes": sum(_size(unit) for unit in units.values()),
            "pagedBytes": sum(t["pagedBytes"] for t in tasks) + _size(envelope), "tasks": tasks}


def read_analysis(analysis_dir, task_id, page, input_sha256):
    inputs, binding, units = _snapshot(analysis_dir)
    if binding != input_sha256:
        raise ValueError("analysis inputs changed; obtain a new plan and restart affected reading")
    task = next((t for t in _tasks(units, inputs["materialIds"]) if t["taskId"] == task_id), None)
    if task is None:
        raise ValueError("unknown analysis task")
    _, pages = _task_pages(task, units)
    if type(page) is not int or not 0 <= page < len(pages):
        raise ValueError("analysis page is out of range")
    return {"format": FORMAT, "inputSha256": binding, "taskId": task_id,
            "sourceFile": task["sourceFile"], "sourcePath": task["sourcePath"],
            "page": page, "pageCount": len(pages), "nextPage": page + 1 if page + 1 < len(pages) else None,
            "records": pages[page]}
