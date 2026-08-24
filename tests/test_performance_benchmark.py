import tempfile
import time
import unittest
from pathlib import Path

from scripts import performance_benchmark


class PerformanceBenchmarkTests(unittest.TestCase):
    def _materials(self):
        return [
            performance_benchmark.MaterialInput(
                material_id=f"m-{index}",
                records_path=Path(f"records-{index}.json"),
                video_path=Path(f"video-{index}.mp4"),
                rpa_fetch_sec=0.001,
            )
            for index in range(4)
        ]

    def test_overlap_starts_next_rpa_wave_before_previous_postprocess_finishes(self):
        started = {}
        release = {}

        def rpa_sleep(_seconds):
            return None

        def postprocess(material, _output_dir):
            started[material.material_id] = time.perf_counter()
            if material.material_id == "m-0":
                release[material.material_id].wait(timeout=1)

        for material in self._materials():
            release[material.material_id] = __import__("threading").Event()
        release["wave-1"] = __import__("threading").Event()

        result_holder = {}

        def run():
            result_holder["result"] = performance_benchmark.run_schedule(
                self._materials(),
                Path(tempfile.mkdtemp()),
                mode="overlap",
                rpa_concurrency=2,
                postprocess_concurrency=2,
                rpa_sleep=rpa_sleep,
                postprocess_fn=postprocess,
                event_hook=lambda event: release["wave-1"].set()
                if event["event"] == "rpa_wave_started" and event["waveIndex"] == 1
                else None,
            )

        import threading

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.time() + 1
        while "m-0" not in started and time.time() < deadline:
            time.sleep(0.005)
        self.assertTrue(release["wave-1"].wait(timeout=1))
        release["m-0"].set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(result_holder["result"]["mode"], "overlap")

    def test_barrier_waits_for_postprocess_before_next_wave(self):
        events = []
        release = __import__("threading").Event()

        def postprocess(material, _output_dir):
            events.append(("postprocess_start", material.material_id))
            if material.material_id == "m-0":
                release.wait(timeout=1)
            events.append(("postprocess_end", material.material_id))

        result_holder = {}

        import threading

        def run():
            result_holder["result"] = performance_benchmark.run_schedule(
                self._materials(),
                Path(tempfile.mkdtemp()),
                mode="barrier",
                rpa_concurrency=2,
                postprocess_concurrency=2,
                rpa_sleep=lambda _seconds: None,
                postprocess_fn=postprocess,
            )

        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.time() + 1
        while ("postprocess_start", "m-0") not in events and time.time() < deadline:
            time.sleep(0.005)
        time.sleep(0.05)
        self.assertNotIn(("postprocess_start", "m-2"), events)
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result_holder["result"]["mode"], "barrier")

    def test_postprocess_group_contains_only_serial_and_parallel_runs(self):
        calls = []

        original_loader = performance_benchmark.load_materials
        original_scheduler = performance_benchmark.run_schedule
        try:
            input_root = Path(tempfile.mkdtemp())
            materials = [
                performance_benchmark.MaterialInput(
                    material_id=f"m-{index}",
                    records_path=input_root / f"records-{index}.json",
                    video_path=input_root / f"video-{index}.mp4",
                    rpa_fetch_sec=0.001,
                )
                for index in range(4)
            ]
            for material in materials:
                material.video_path.write_bytes(b"video")
            performance_benchmark.load_materials = lambda _source, _count: ({}, materials)

            def fake_scheduler(materials, output_dir, **kwargs):
                calls.append((Path(output_dir).name, kwargs["postprocess_concurrency"]))
                return {"mode": kwargs["mode"], "totalSec": 1.0, "materials": [], "failedCount": 0}

            performance_benchmark.run_schedule = fake_scheduler
            output = Path(tempfile.mkdtemp()) / "postprocess"
            result = performance_benchmark.run_postprocess_benchmark(
                "source", output, material_count=4
            )
        finally:
            performance_benchmark.load_materials = original_loader
            performance_benchmark.run_schedule = original_scheduler

        self.assertEqual(calls, [("postprocess-serial", 1), ("postprocess-parallel", 3)])
        self.assertNotIn("scheduler", result)

    def test_default_postprocess_can_download_from_retained_video_url(self):
        root = Path(tempfile.mkdtemp())
        records_path = root / "records.json"
        records_path.write_text(
            '[{"recordType":"material_info","materialId":"m-1",'
            '"videoUrl":"https://example.test/m-1.mp4"}]',
            encoding="utf-8",
        )
        material = performance_benchmark.MaterialInput(
            material_id="m-1",
            records_path=records_path,
            video_path=root / "cached.mp4",
            rpa_fetch_sec=0,
        )
        calls = []
        original_download = performance_benchmark.download_video
        original_process = performance_benchmark.process_records
        try:
            performance_benchmark.download_video = lambda url, target: (
                calls.append((url, target.name)), target.write_bytes(b"video")
            )[-1]
            performance_benchmark.process_records = lambda *args, **kwargs: (
                root,
                root / "summary.json",
                root / "summary.md",
            )
            (root / "summary.json").write_text('{"frames":[],"missingFrames":[]}', encoding="utf-8")
            result = performance_benchmark._default_postprocess(
                material, root / "output", download_videos=True
            )
        finally:
            performance_benchmark.download_video = original_download
            performance_benchmark.process_records = original_process

        self.assertEqual(calls[0][0], "https://example.test/m-1.mp4")
        self.assertEqual(result["frameCount"], 0)


if __name__ == "__main__":
    unittest.main()
