import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from final_report_lib import FinalReportError, load_conclusion_analysis  # noqa: E402


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class FinalReportConclusionTests(unittest.TestCase):
    def test_accepts_valid_conclusion_schema(self):
        payload = {
            "schemaVersion": 1,
            "overallConclusion": "礼赠和滋补卖点都值得保留。",
            "keyInsights": [
                {"title": "礼盒开箱强", "body": "高光帧集中在包装动作。", "materialIds": ["m-1"]}
            ],
            "materialRecommendations": [
                {
                    "materialId": "m-1",
                    "decision": "scale",
                    "reason": "消耗和点击峰值都集中。",
                    "nextAction": "保留前 5 秒开箱动作。",
                }
            ],
            "nextActions": [
                {"title": "新拍礼赠版", "body": "强化送礼对象。", "priority": "high"}
            ],
            "warnings": ["结论来自外部分析。"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "diagnostic-ai-analysis.json"
            write_json(path, payload)

            result = load_conclusion_analysis(path)

        self.assertEqual(result["overallConclusion"], "礼赠和滋补卖点都值得保留。")
        self.assertEqual(result["materialRecommendations"][0]["decision"], "scale")

    def test_rejects_invalid_conclusion_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "diagnostic-ai-analysis.json"
            write_json(path, {"schemaVersion": 2, "overallConclusion": "bad"})

            with self.assertRaisesRegex(FinalReportError, "unsupported conclusion schemaVersion"):
                load_conclusion_analysis(path)
