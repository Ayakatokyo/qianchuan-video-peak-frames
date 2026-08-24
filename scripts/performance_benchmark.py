"""Replay retained RPA material data for scheduler and postprocess benchmarks.

This module intentionally does not call the RPA connector. It validates the
retained material_info.videoUrl values, reuses the corresponding local video
cache, and measures the current postprocess implementation under two scheduler
policies plus serial/parallel postprocess pools.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from peak_frame_lib import process_records
from rpa_adapter import download_video, find_material_video_url


@dataclass(frozen=True)
class MaterialInput:
    material_id: str
    records_path: Path
    video_path: Path
    rpa_fetch_sec: float


def _now():
    return time.perf_counter()


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _default_postprocess(material, output_dir, *, download_videos=False):
    records = json.loads(material.records_path.read_text(encoding="utf-8"))
    # URL resolution is part of the benchmark input contract even when the
    # downloaded bytes are reused from the retained cache.
    find_material_video_url(records, material.material_id)
    started = _now()
    if download_videos:
        with tempfile.TemporaryDirectory(prefix=f"perf-{material.material_id}-") as temp_dir:
            video_path = Path(temp_dir) / f"{material.material_id}.mp4"
            download_video(find_material_video_url(records, material.material_id), video_path)
            result_dir, summary_json, summary_md = process_records(
                records,
                f"retained-rpa://{material.material_id}",
                video_path,
                output_dir,
                video_source="retained-video-url",
            )
    else:
        if not material.video_path.is_file() or material.video_path.stat().st_size <= 0:
            raise RuntimeError(f"cached video is missing: {material.video_path}")
        result_dir, summary_json, summary_md = process_records(
            records,
            f"retained-rpa://{material.material_id}",
            material.video_path,
            output_dir,
            video_source="retained-video-cache",
        )
    summary = json.loads(Path(summary_json).read_text(encoding="utf-8"))
    return {
        "resultDir": str(result_dir),
        "summaryJson": str(summary_json),
        "summaryMd": str(summary_md),
        "frameCount": len(summary.get("frames", [])),
        "missingFrameCount": len(summary.get("missingFrames", [])),
        "durationSec": _now() - started,
    }


def _material_result(material, output_dir, postprocess_fn):
    started = _now()
    try:
        result = postprocess_fn(material, output_dir)
        return {
            "materialId": material.material_id,
            "status": "succeeded",
            "startedAtSec": None,
            "finishedAtSec": None,
            "durationSec": _now() - started,
            "result": result,
        }
    except Exception as exc:  # benchmark must retain per-material failures
        return {
            "materialId": material.material_id,
            "status": "failed",
            "startedAtSec": None,
            "finishedAtSec": None,
            "durationSec": _now() - started,
            "errorType": type(exc).__name__,
            "errorMessage": str(exc),
        }


def run_schedule(
    materials,
    output_dir,
    *,
    mode,
    rpa_concurrency=3,
    postprocess_concurrency=3,
    rpa_delay_scale=0.02,
    rpa_sleep=time.sleep,
    postprocess_fn=None,
    event_hook=None,
    download_videos=False,
):
    """Run a retained-data scheduler replay and return timestamped metrics."""
    if mode not in {"barrier", "overlap"}:
        raise ValueError("mode must be barrier or overlap")
    if not materials:
        raise ValueError("materials must not be empty")
    if rpa_concurrency <= 0 or postprocess_concurrency <= 0:
        raise ValueError("concurrency must be positive")
    if postprocess_fn is None:
        postprocess_fn = lambda material, output: _default_postprocess(
            material, output, download_videos=download_videos
        )
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = _now()
    events = []
    all_postprocess = []
    material_by_id = {material.material_id: material for material in materials}

    def event(name, material_id=None, wave_index=None):
        events.append(
            {
                "event": name,
                "materialId": material_id,
                "waveIndex": wave_index,
                "atSec": round(_now() - started, 6),
            }
        )
        if callable(event_hook):
            event_hook(events[-1])

    def rpa_ready(material, wave_index):
        delay = max(0.0, float(material.rpa_fetch_sec or 0.0)) * rpa_delay_scale
        rpa_sleep(delay)
        event("records_persisted", material.material_id, wave_index)
        return material

    def postprocess(material, wave_index):
        event("postprocess_started", material.material_id, wave_index)
        result = _material_result(material, output_dir, postprocess_fn)
        event("postprocess_finished", material.material_id, wave_index)
        return result

    waves = [
        list(materials[index : index + rpa_concurrency])
        for index in range(0, len(materials), rpa_concurrency)
    ]
    with ThreadPoolExecutor(max_workers=rpa_concurrency) as rpa_pool, ThreadPoolExecutor(
        max_workers=postprocess_concurrency
    ) as postprocess_pool:
        for wave_index, wave in enumerate(waves):
            event("rpa_wave_started", wave_index=wave_index)
            futures = {
                rpa_pool.submit(rpa_ready, material, wave_index): material
                for material in wave
            }
            wave_postprocess = []
            for future in as_completed(futures):
                material = future.result()
                wave_postprocess.append(
                    postprocess_pool.submit(postprocess, material, wave_index)
                )
            all_postprocess.extend(wave_postprocess)
            event("rpa_wave_finished", wave_index=wave_index)
            if mode == "barrier":
                for future in wave_postprocess:
                    future.result()
                event("postprocess_wave_finished", wave_index=wave_index)
        if mode == "overlap":
            for future in all_postprocess:
                future.result()
    finished = _now()
    postprocess_results = [future.result() for future in all_postprocess]
    return {
        "mode": mode,
        "materialCount": len(materials),
        "rpaConcurrency": rpa_concurrency,
        "postprocessConcurrency": postprocess_concurrency,
        "rpaDelayScale": rpa_delay_scale,
        "startedAtEpoch": time.time(),
        "totalSec": round(finished - started, 6),
        "events": events,
        "materials": postprocess_results,
        "failedCount": sum(item["status"] != "succeeded" for item in postprocess_results),
        "inputMaterialIds": list(material_by_id),
    }


def load_materials(source_run, material_count):
    source_run = Path(source_run)
    manifest_path = source_run / "peak-frames" / "batch-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selected = [
        item
        for item in manifest.get("materials", [])
        if item.get("rpaStatus") == "selected"
        and item.get("recordsPersisted") is True
        and item.get("postprocessStatus") == "succeeded"
    ]
    selected.sort(key=lambda item: (item.get("rank", 10**9), item.get("materialId", "")))
    selected = selected[:material_count]
    if len(selected) != material_count:
        raise RuntimeError(
            f"source run has only {len(selected)} reusable selected materials; "
            f"requested {material_count}"
        )
    cache_dir = source_run / "benchmark" / "video-cache"
    materials = []
    for item in selected:
        material_id = str(item["materialId"])
        records_path = source_run / "diagnosis-records" / f"{material_id}.json"
        video_path = cache_dir / f"{material_id}.mp4"
        records = json.loads(records_path.read_text(encoding="utf-8"))
        video_url = find_material_video_url(records, material_id)
        if not video_url.startswith(("http://", "https://")):
            raise RuntimeError(f"material {material_id} has an invalid videoUrl")
        if not video_path.is_file() or video_path.stat().st_size <= 0:
            raise RuntimeError(f"material {material_id} has no retained video cache")
        materials.append(
            MaterialInput(
                material_id=material_id,
                records_path=records_path,
                video_path=video_path,
                rpa_fetch_sec=float(item.get("timingsSec", {}).get("rpaFetchSec") or 0),
            )
        )
    return manifest, materials


def run_benchmark(source_run, output_dir, material_count=9):
    manifest, materials = load_materials(source_run, material_count)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    common = {
        "rpa_concurrency": 3,
        "postprocess_concurrency": 3,
        "rpa_delay_scale": 0.02,
    }
    barrier = run_schedule(materials, output_dir / "scheduler-barrier", mode="barrier", **common)
    overlap = run_schedule(materials, output_dir / "scheduler-overlap", mode="overlap", **common)
    serial = run_schedule(
        materials,
        output_dir / "postprocess-serial",
        mode="overlap",
        rpa_concurrency=3,
        postprocess_concurrency=1,
        rpa_delay_scale=0,
    )
    parallel = run_schedule(
        materials,
        output_dir / "postprocess-parallel",
        mode="overlap",
        rpa_concurrency=3,
        postprocess_concurrency=3,
        rpa_delay_scale=0,
    )
    result = {
        "sourceRun": str(Path(source_run).resolve()),
        "sourceManifest": {
            "schemaVersion": manifest.get("schemaVersion"),
            "runId": manifest.get("runId"),
            "dateRange": manifest.get("dateRange"),
            "targetTopN": manifest.get("targetTopN"),
            "sourceWaveBarrier": manifest.get("waveBarrier"),
            "stageCounts": manifest.get("stageCounts"),
        },
        "materialCount": len(materials),
        "materialIds": [material.material_id for material in materials],
        "inputEvidence": [
            {
                "materialId": material.material_id,
                "recordsPath": str(material.records_path),
                "videoPath": str(material.video_path),
                "videoBytes": material.video_path.stat().st_size,
                "videoSha256": _sha256(material.video_path),
                "rpaFetchSec": material.rpa_fetch_sec,
            }
            for material in materials
        ],
        "scheduler": {
            "barrier": barrier,
            "overlap": overlap,
            "wallClockSpeedup": barrier["totalSec"] / overlap["totalSec"]
            if overlap["totalSec"]
            else None,
            "secondsSaved": barrier["totalSec"] - overlap["totalSec"],
        },
        "postprocessPool": {
            "serial": serial,
            "parallel": parallel,
            "wallClockSpeedup": serial["totalSec"] / parallel["totalSec"]
            if parallel["totalSec"]
            else None,
            "secondsSaved": serial["totalSec"] - parallel["totalSec"],
        },
        "method": {
            "schedulerReplay": "retained records + compressed original rpaFetchSec ordering",
            "videoInput": "retained material_info.videoUrl validated; retained local MP4 bytes reused",
            "aiAnalysis": False,
            "reportGeneration": False,
        },
    }
    _write_json(output_dir / "comparison.json", result)
    return result


def run_postprocess_benchmark(source_run, output_dir, material_count=9):
    """Compare only serial and parallel postprocess on retained real inputs."""
    manifest, materials = load_materials(source_run, material_count)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    serial = run_schedule(
        materials,
        output_dir / "postprocess-serial",
        mode="overlap",
        rpa_concurrency=3,
        postprocess_concurrency=1,
        rpa_delay_scale=0,
        download_videos=True,
    )
    parallel = run_schedule(
        materials,
        output_dir / "postprocess-parallel",
        mode="overlap",
        rpa_concurrency=3,
        postprocess_concurrency=3,
        rpa_delay_scale=0,
        download_videos=True,
    )
    result = {
        "sourceRun": str(Path(source_run).resolve()),
        "sourceManifest": {
            "schemaVersion": manifest.get("schemaVersion"),
            "runId": manifest.get("runId"),
            "dateRange": manifest.get("dateRange"),
            "targetTopN": manifest.get("targetTopN"),
            "sourceWaveBarrier": manifest.get("waveBarrier"),
            "stageCounts": manifest.get("stageCounts"),
        },
        "materialCount": len(materials),
        "materialIds": [material.material_id for material in materials],
        "inputEvidence": [
            {
                "materialId": material.material_id,
                "recordsPath": str(material.records_path),
                "videoPath": str(material.video_path),
                "videoBytes": material.video_path.stat().st_size,
                "videoSha256": _sha256(material.video_path),
                "rpaFetchSec": material.rpa_fetch_sec,
            }
            for material in materials
        ],
        "postprocessPool": {
            "serial": serial,
            "parallel": parallel,
            "wallClockSpeedup": serial["totalSec"] / parallel["totalSec"]
            if parallel["totalSec"]
            else None,
            "secondsSaved": serial["totalSec"] - parallel["totalSec"],
        },
        "method": {
            "input": "retained diagnosis-records JSON and validated material_info.videoUrl",
            "videoInput": "each retained material_info.videoUrl downloaded afresh per material",
            "postprocessOnly": True,
            "aiAnalysis": False,
            "reportGeneration": False,
        },
    }
    _write_json(output_dir / "comparison.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--material-count", type=int, default=9)
    parser.add_argument("--group", choices=("postprocess", "scheduler", "all"), default="postprocess")
    args = parser.parse_args(argv)
    if args.group == "postprocess":
        result = run_postprocess_benchmark(args.source_run, args.output_dir, args.material_count)
    elif args.group == "scheduler":
        manifest, materials = load_materials(args.source_run, args.material_count)
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=False)
        common = {"rpa_concurrency": 3, "postprocess_concurrency": 3, "rpa_delay_scale": 0.02}
        barrier = run_schedule(materials, output / "scheduler-barrier", mode="barrier", **common)
        overlap = run_schedule(materials, output / "scheduler-overlap", mode="overlap", **common)
        result = {"sourceRun": str(Path(args.source_run).resolve()), "materialCount": len(materials),
                  "scheduler": {"barrier": barrier, "overlap": overlap}}
        _write_json(output / "comparison.json", result)
    else:
        result = run_benchmark(args.source_run, args.output_dir, args.material_count)
    print(json.dumps({
        "outputDir": str(Path(args.output_dir).resolve()),
        "materialCount": result["materialCount"],
        "postprocessPool": {
            "serialSec": result.get("postprocessPool", {}).get("serial", {}).get("totalSec"),
            "parallelSec": result.get("postprocessPool", {}).get("parallel", {}).get("totalSec"),
            "speedup": result.get("postprocessPool", {}).get("wallClockSpeedup"),
        } if "postprocessPool" in result else None,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
