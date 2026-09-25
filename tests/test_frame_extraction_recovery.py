"""Exact media extraction and interrupted frame publication regressions."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import peak_frame_lib as media


@pytest.fixture
def clip(tmp_path):
    ffmpeg = media.resolve_ffmpeg()
    video = tmp_path / "long GOP with B frames.mp4"
    subprocess.run([
        ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
        "testsrc2=s=128x128:r=25:d=12", "-c:v", "libx264", "-preset", "ultrafast",
        "-g", "100", "-bf", "3", "-y", str(video),
    ], check=True, capture_output=True, timeout=30)
    return ffmpeg, video


def test_batch_handles_tiny_and_fractional_timestamps_exactly(clip, tmp_path):
    ffmpeg, video = clip
    timestamps = [0.0, 0.0000009, 0.19, 2.03, 8.001, 11.94]
    outputs = [tmp_path / f"batch-{index}.jpg" for index in range(len(timestamps))]
    media.extract_frames_batch(ffmpeg, video, list(zip(timestamps, outputs)))
    for index, timestamp in enumerate(timestamps):
        reference = tmp_path / f"reference-{index}.jpg"
        media.extract_frame(ffmpeg, video, timestamp, reference)
        assert outputs[index].read_bytes() == reference.read_bytes(), timestamp


@pytest.mark.parametrize("variant", ["variable_rate", "nonzero_start", "ntsc_rate"])
def test_seek_preserves_frame_identity_for_media_time_bases(tmp_path, variant):
    ffmpeg = media.resolve_ffmpeg()
    video = tmp_path / f"{variant}.mp4"
    rate = "30000/1001" if variant == "ntsc_rate" else "25"
    command = [ffmpeg, "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s=96x96:r={rate}:d=12"]
    if variant == "variable_rate":
        command.extend(["-vf", r"select=not(mod(n\,3))+gte(t\,6)", "-fps_mode", "vfr"])
    command.extend(["-c:v", "libx264", "-preset", "ultrafast", "-g", "100", "-bf", "3"])
    if variant == "nonzero_start":
        command.extend(["-output_ts_offset", "3"])
    subprocess.run(command + ["-y", str(video)], check=True, capture_output=True, timeout=30)
    timestamps = [0.0, 0.04, 0.041, 2.03, 6.123, 11.94]
    targets = [(stamp, tmp_path / f"actual-{index}.jpg") for index, stamp in enumerate(timestamps)]
    media.extract_frames_batch(ffmpeg, video, targets)
    for index, (stamp, actual) in enumerate(targets):
        reference = tmp_path / f"reference-{index}.jpg"
        media.extract_frame(ffmpeg, video, stamp, reference)
        assert actual.read_bytes() == reference.read_bytes(), (variant, stamp)


def _records():
    return [{
        "recordType": "interaction_series", "interactionMetric": "CLICK_COUNT",
        "materialId": "m-cache", "durationSec": second, "metricValue": value,
    } for second, value in enumerate([1, 0, 9, 0, 5, 0])]


def test_resume_reuses_published_frames_after_interruption(clip, tmp_path, monkeypatch):
    ffmpeg, video = clip
    original = media.extract_frames_batch
    published = []

    def interrupt_after_first(binary, source, targets, **kwargs):
        first = targets[:1]
        original(binary, source, first)
        published.append(first[0][1])
        if kwargs.get("on_frame"):
            kwargs["on_frame"](first[0][1])
        raise KeyboardInterrupt("process ended before the remaining JPEGs")

    monkeypatch.setattr(media, "extract_frames_batch", interrupt_after_first)
    with pytest.raises(KeyboardInterrupt):
        media.process_records(_records(), "records.json", video, tmp_path / "out", ffmpeg)
    cover = published[0]
    original_stat = cover.stat()
    monkeypatch.setattr(media, "extract_frames_batch", original)
    _, summary, _ = media.process_records(
        _records(), "records.json", video, tmp_path / "out", ffmpeg,
    )
    assert cover.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert cover.stat().st_ino == original_stat.st_ino
    assert len(json.loads(summary.read_text())["frames"]) == 3


def test_frame_cache_rejects_corruption_and_changed_video(clip, tmp_path):
    ffmpeg, video = clip
    output = tmp_path / "out"
    _, summary, _ = media.process_records(_records(), "records.json", video, output, ffmpeg)
    frames = [summary.parent / frame["framePath"] for frame in json.loads(summary.read_text())["frames"]]
    unchanged = frames[0].stat().st_mtime_ns
    expected = frames[1].read_bytes()
    frames[1].write_bytes(b"invalid JPEG")
    media.process_records(_records(), "records.json", video, output, ffmpeg)
    assert frames[0].stat().st_mtime_ns == unchanged
    assert frames[1].read_bytes() == expected
    # Replacing input bytes invalidates every cached frame even if the path is unchanged.
    video.write_bytes(video.read_bytes() + b"changed source fingerprint")
    media.process_records(_records(), "records.json", video, output, ffmpeg)
    assert frames[0].stat().st_mtime_ns != unchanged


def test_frame_cache_does_not_reuse_a_different_peak_selection(clip, tmp_path):
    ffmpeg, video = clip
    records = _records()
    _, summary, _ = media.process_records(records, "records.json", video, tmp_path / "out", ffmpeg)
    original_frames = json.loads(summary.read_text())["frames"]
    old_peak = summary.parent / original_frames[1]["framePath"]
    records[2]["metricValue"] = 3
    media.process_records(records, "records.json", video, tmp_path / "out", ffmpeg)
    final_frames = json.loads(summary.read_text())["frames"]
    assert [frame["durationSec"] for frame in final_frames] == [0.0, 4.0, 2.0]
    assert not old_peak.exists()


def test_damaged_checkpoint_is_rebuilt_without_trusting_unverified_jpegs(clip, tmp_path):
    ffmpeg, video = clip
    output = tmp_path / "out"
    _, summary, _ = media.process_records(_records(), "records.json", video, output, ffmpeg)
    cover = summary.parent / json.loads(summary.read_text())["frames"][0]["framePath"]
    previous_mtime = cover.stat().st_mtime_ns
    (summary.parent / "frame-extraction-state.json").write_text("{truncated")
    media.process_records(_records(), "records.json", video, output, ffmpeg)
    assert cover.stat().st_mtime_ns != previous_mtime
    assert json.loads((summary.parent / "frame-extraction-state.json").read_text())["schemaVersion"] == 1
