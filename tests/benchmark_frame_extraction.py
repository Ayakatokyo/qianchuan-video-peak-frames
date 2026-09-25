"""Offline synthetic comparison of the previous trim batch and accurate seeks.

Run from the repository with its Python environment. The generated clip and all
JPEGs live in a TemporaryDirectory; no customer files or network are used.
Results are measurements, not a timing threshold in the ordinary test suite.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from peak_frame_lib import extract_frames_batch, resolve_ffmpeg


def previous_trim_batch(ffmpeg, video, targets):
    labels = [f"s{index}" for index in range(len(targets))]
    filters = [f"[0:v]split={len(targets)}" + "".join(f"[{label}]" for label in labels)]
    filters.extend(
        f"[{labels[index]}]trim=start={timestamp},setpts=PTS-STARTPTS[o{index}]"
        for index, (timestamp, _output) in enumerate(targets)
    )
    command = [ffmpeg, "-v", "error", "-threads", "1", "-filter_threads", "1",
               "-t", str(max(stamp for stamp, _ in targets) + 1), "-i", str(video),
               "-filter_complex", ";".join(filters)]
    for index, (_stamp, path) in enumerate(targets):
        command.extend(["-map", f"[o{index}]", "-frames:v", "1", "-q:v", "2", "-y", str(path)])
    subprocess.run(command, capture_output=True, check=True, timeout=180)


def benchmark(*, width=1080, height=1920, duration=60, repeats=3):
    ffmpeg = resolve_ffmpeg()
    version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    timestamps = [round(duration * ratio, 6) for ratio in (0, .15, .35, .55, .75, .95)]
    elapsed = {"previousTrim": [], "accurateSeek": []}
    methods = [("previousTrim", previous_trim_batch), ("accurateSeek", extract_frames_batch)]
    with tempfile.TemporaryDirectory(prefix="peak-frame-benchmark-") as temporary:
        root = Path(temporary)
        video = root / "synthetic.mp4"
        subprocess.run([
            ffmpeg, "-v", "error", "-f", "lavfi", "-i",
            f"testsrc2=s={width}x{height}:r=25:d={duration}", "-c:v", "libx264",
            "-preset", "ultrafast", "-g", "50", "-bf", "3", "-threads", "2", "-y", str(video),
        ], capture_output=True, check=True, timeout=180)
        video_bytes = video.stat().st_size
        expected = None
        for iteration in range(repeats):
            # Alternate order to avoid always giving one implementation warm I/O.
            for name, extract in methods[::1 if iteration % 2 == 0 else -1]:
                targets = [(stamp, root / f"{name}-{iteration}-{index}.jpg")
                           for index, stamp in enumerate(timestamps)]
                started = time.perf_counter()
                extract(ffmpeg, video, targets)
                elapsed[name].append(round(time.perf_counter() - started, 6))
                hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for _stamp, path in targets]
                if expected is None:
                    expected = hashes
                if hashes != expected:
                    raise AssertionError("extracted JPEG bytes differ from the previous trim implementation")
    median = {name: round(statistics.median(values), 6) for name, values in elapsed.items()}
    return {"platform": platform.platform(), "ffmpegVersion": version,
            "fixture": {"width": width, "height": height, "durationSec": duration,
                        "fps": 25, "gopFrames": 50, "bFrames": 3, "bytes": video_bytes},
            "timestamps": timestamps, "repeats": repeats, "seconds": elapsed, "medianSec": median,
            "speedup": round(median["previousTrim"] / median["accurateSeek"], 3),
            "identicalFullResolutionJpegs": True, "jpegSha256": expected}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(benchmark(**vars(args)), ensure_ascii=False, indent=2))
