from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "SKILL.md"


class BatchSkillContractTests(unittest.TestCase):
    def test_frontmatter_is_chinese(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("name: 千川短视频素材分析", skill)
        self.assertIn("description: 面向千川短视频投放复盘", skill)
        self.assertIn("素材优劣判断", skill)
        self.assertIn("投放优化决策", skill)
        frontmatter = skill.split("---", 2)[1]
        description_line = next(
            line for line in frontmatter.splitlines() if line.startswith("description:")
        )
        self.assertNotIn("资产表", description_line)
        self.assertNotIn("RPA", description_line)
        self.assertNotIn("CLICK_COUNT", description_line)
        self.assertNotIn("诊断数据拉取", description_line)
        self.assertIn("argument_hint:", skill)
        self.assertIn("生成千川短视频素材分析报告", skill)
        self.assertNotIn("生成千川短视频闭环分析报告", skill)
        self.assertIn("分析周期", skill)
        self.assertIn("Top素材数量", skill)
        self.assertIn("广告主id", skill)
        self.assertIn("metadata:", skill)
        self.assertIn("pre_input:", skill)
        self.assertIn("分析周期是 ____", skill)
        self.assertIn("Top素材数量是 ____", skill)
        self.assertIn("广告主id是 ____", skill)

    def test_batch_workflow_exports_and_reuses_run_local_top_materials(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("## TOP 素材批量运行", skill)
        batch_start = skill.index("## TOP 素材批量运行")
        export_start = skill.index(
            "python scripts/export_top_materials.py", batch_start
        )
        rpa_start = skill.index(
            "python scripts/extract_peak_frames.py", batch_start
        )

        self.assertLess(export_start, rpa_start)
        self.assertIn('--output "/path/to/run/top_materials.json"', skill)
        self.assertIn(
            '--top-materials-json "/path/to/run/top_materials.json"', skill
        )
        self.assertIn("--custom-start-date 2026-06-01", skill)
        self.assertIn("--custom-end-date 2026-06-07", skill)

    def test_batch_workflow_requires_advertiser_id_for_asset_selection(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("--advertiser-id", skill)
        self.assertIn("广告主 id", skill)
        self.assertIn("filters.advertiserId", skill)
        self.assertIn("adv_id", skill)
        self.assertIn("COLUMN_NOT_EXIST", skill)
        self.assertIn("不要把资产表过滤字段写成 `advertiser_id`", skill)

    def test_single_material_rpa_is_documented_as_debugging_only(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("## TOP 素材批量运行", skill)
        batch_start = skill.index("## TOP 素材批量运行")
        single_start = skill.index("## 单素材 RPA 调试运行")

        self.assertLess(batch_start, single_start)
        self.assertIn(
            "不是 TOP 素材批量运行的替代选材方式",
            skill,
        )

    def test_files_response_documents_retained_csv_artifact(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("diagnosis-csv/<materialId>.csv", skill)
        self.assertIn("CSV 下载或解析失败仍会阻塞流程", skill)
        self.assertNotIn(
            "result_type=files` CSV-envelope response is blocking", skill
        )

    def test_batch_resume_documents_manifest_and_partial_failure(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("batch-manifest.json", skill)
        self.assertIn("--resume", skill)
        self.assertIn("后续 TOP 素材仍会继续运行", skill)

    def test_batch_verification_documents_receipt_and_failure_exit_code(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("--verify", skill)
        self.assertIn("batch-verification.json", skill)
        self.assertIn("退出码 `6`", skill)

    def test_batch_concurrency_and_timing_receipt_are_documented(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("--concurrency 3", skill)
        self.assertIn("受控并行", skill)
        self.assertIn("timingsSec", skill)
        self.assertIn("rpaFetchSec", skill)
        self.assertIn("frameExtractionSec", skill)

    def test_final_html_report_loop_is_documented(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("## 最终 HTML 报告流程", skill)
        self.assertIn("prepare_diagnostic_analysis.py", skill)
        self.assertIn("diagnostic-ai-analysis.json", skill)
        self.assertIn("render_final_report.py", skill)
        self.assertIn("不会调用外部 API", skill)


if __name__ == "__main__":
    unittest.main()
