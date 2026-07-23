from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from rpa_csv_parser import RpaCsvError, parse_diagnosis_csv  # noqa: E402


class RpaCsvParserTests(unittest.TestCase):
    def _write_csv(self, content):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "diagnosis.csv"
        path.write_text(content, encoding="utf-8")
        return path

    def test_parses_rpa_control_delimited_rows_into_records(self):
        path = self._write_csv(
            "recordType\x01materialId\x01interactionMetric\x01durationSec\x01metricValue\n"
            "\x02interaction_series\x01m-1\x01CLICK_COUNT\x011.0\x012.0\n"
        )

        records = parse_diagnosis_csv(path)

        self.assertEqual(records, [
            {
                "recordType": "interaction_series",
                "materialId": "m-1",
                "interactionMetric": "CLICK_COUNT",
                "durationSec": 1.0,
                "metricValue": 2.0,
            }
        ])

    def test_normalizes_dimensions_and_uses_single_metrics_value(self):
        path = self._write_csv(
            "recordType\x01materialId\x01displayMetric\x01dimensionKey\x01Dimensions\x01Metrics\x01metricValue\n"
            "\x02crowd_analysis\x01m-1\x01SHOW_COUNT\x01age\x01"
            "{'age': {'Value': '2', 'ValueStr': '24-30'}}\x01"
            "{'live_show_count': {'Value': 42.5, 'ValueStr': '42.5'}}\x01\n"
        )

        record = parse_diagnosis_csv(path)[0]

        self.assertEqual(record["Dimensions"]["age"]["ValueStr"], "24-30")
        self.assertEqual(record["Metrics"]["live_show_count"]["Value"], 42.5)
        self.assertEqual(record["metricValue"], 42.5)

    def test_rejects_ambiguous_crowd_metrics(self):
        path = self._write_csv(
            "recordType\x01displayMetric\x01Metrics\x01metricValue\n"
            "\x02crowd_analysis\x01SHOW_COUNT\x01"
            "{'first': {'Value': 1}, 'second': {'Value': 2}}\x01\n"
        )

        with self.assertRaisesRegex(RpaCsvError, "multiple numeric Metrics"):
            parse_diagnosis_csv(path)

    def test_rejects_a_standard_comma_delimited_csv(self):
        path = self._write_csv(
            "recordType,materialId,interactionMetric,durationSec,metricValue\n"
            "interaction_series,m-1,CLICK_COUNT,1,2\n"
        )

        with self.assertRaisesRegex(RpaCsvError, r"\\x01"):
            parse_diagnosis_csv(path)


if __name__ == "__main__":
    unittest.main()
