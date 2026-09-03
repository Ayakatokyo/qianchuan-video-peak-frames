import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rpa_artifact_receipts import (  # noqa: E402
    RpaArtifactReceiptError,
    load_artifact_receipt,
    load_records_persisted_receipt,
    publish_receipt,
)


class RpaArtifactReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.material_id = "m-123"
        self.attempt_id = "attempt-123"
        self.csv_path = self.root / "diagnosis-csv" / f"{self.material_id}.csv"
        self.records_path = (
            self.root / "diagnosis-records" / f"{self.material_id}.json"
        )
        self.csv_path.parent.mkdir()
        self.records_path.parent.mkdir()
        self.csv_path.write_text(
            "recordType\x01materialId\x01videoUrl\n"
            f"material_info\x01{self.material_id}\x01https://video.example/video.mp4\n",
            encoding="utf-8",
        )
        self.records = [
            {
                "recordType": "material_info",
                "materialId": self.material_id,
                "videoUrl": "https://video.example/video.mp4",
            }
        ]
        self.records_path.write_text(
            json.dumps(self.records), encoding="utf-8"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_records_receipt_contains_durable_artifact_metadata(self):
        receipts_dir = self.root / "rpa-receipts"

        receipt = publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="records_persisted",
            csv_path=self.csv_path,
            records_path=self.records_path,
        )

        self.assertEqual(receipt["schemaVersion"], 2)
        self.assertEqual(receipt["materialId"], self.material_id)
        self.assertEqual(receipt["clientAttemptId"], self.attempt_id)
        self.assertEqual(receipt["state"], "records_persisted")
        self.assertEqual(receipt["csvPath"], str(self.csv_path))
        self.assertEqual(receipt["recordsPath"], str(self.records_path))
        self.assertEqual(receipt["csvSizeBytes"], self.csv_path.stat().st_size)
        self.assertEqual(
            receipt["csvSha256"], hashlib.sha256(self.csv_path.read_bytes()).hexdigest()
        )
        self.assertIsInstance(receipt["artifactDownloadedAt"], str)
        self.assertIsInstance(receipt["recordsPersistedAt"], str)
        self.assertTrue((receipts_dir / f"{self.material_id}.json").is_file())

    def test_loader_keeps_read_only_compatibility_with_schema_v1(self):
        receipts_dir = self.root / "rpa-receipts"
        receipt = publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="records_persisted",
            csv_path=self.csv_path,
            records_path=self.records_path,
        )
        receipt["schemaVersion"] = 1
        receipt.pop("remoteTaskGroupId", None)
        (receipts_dir / f"{self.material_id}.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )

        recovered = load_records_persisted_receipt(
            receipts_dir, material_id=self.material_id
        )

        self.assertEqual(recovered["receipt"]["schemaVersion"], 1)

    def test_receipt_lifecycle_retains_artifact_timestamp_when_records_arrive(self):
        receipts_dir = self.root / "rpa-receipts"
        artifact = publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="artifact_downloaded",
            csv_path=self.csv_path,
        )

        records = publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="records_persisted",
            csv_path=self.csv_path,
            records_path=self.records_path,
        )

        self.assertEqual(records["artifactDownloadedAt"], artifact["artifactDownloadedAt"])
        self.assertNotEqual(records["recordsPersistedAt"], None)

    def test_receipt_retains_remote_task_group_id_through_lifecycle(self):
        receipts_dir = self.root / "rpa-receipts"
        publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="submitted",
            remote_task_group_id="group-1",
        )
        publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="artifact_downloaded",
            csv_path=self.csv_path,
        )
        receipt = publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="records_persisted",
            csv_path=self.csv_path,
            records_path=self.records_path,
        )

        self.assertEqual(receipt["remoteTaskGroupId"], "group-1")

    def test_loader_reads_verified_downloaded_csv_before_records_persist(self):
        receipts_dir = self.root / "rpa-receipts"
        publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="artifact_downloaded",
            csv_path=self.csv_path,
        )

        recovered = load_artifact_receipt(
            receipts_dir, material_id=self.material_id
        )

        self.assertEqual(recovered["receipt"]["state"], "artifact_downloaded")
        self.assertEqual(recovered["csvPath"], self.csv_path)
        self.assertIsNone(recovered["recordsPath"])

    def test_loader_rejects_csv_digest_mismatch(self):
        receipts_dir = self.root / "rpa-receipts"
        publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="records_persisted",
            csv_path=self.csv_path,
            records_path=self.records_path,
        )
        self.csv_path.write_bytes(b"x" * self.csv_path.stat().st_size)

        with self.assertRaisesRegex(RpaArtifactReceiptError, "digest"):
            load_records_persisted_receipt(
                receipts_dir,
                material_id=self.material_id,
            )

    def test_loader_rejects_records_for_a_different_material(self):
        receipts_dir = self.root / "rpa-receipts"
        self.records_path.write_text(
            json.dumps([{"materialId": "m-other"}]), encoding="utf-8"
        )
        publish_receipt(
            receipts_dir,
            material_id=self.material_id,
            client_attempt_id=self.attempt_id,
            state="records_persisted",
            csv_path=self.csv_path,
            records_path=self.records_path,
        )

        with self.assertRaisesRegex(RpaArtifactReceiptError, "records"):
            load_records_persisted_receipt(
                receipts_dir,
                material_id=self.material_id,
            )


if __name__ == "__main__":
    unittest.main()
