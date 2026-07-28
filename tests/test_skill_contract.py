from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "SKILL.md"
RPA_CONTRACT_PATH = ROOT / "references" / "rpa-output-contract.md"
DIAGNOSTIC_PROMPT_PATH = ROOT / "prompts" / "diagnostic-analysis-prompt-v1.md"


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

    def test_batch_workflow_streams_backfill_and_postprocess(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        contract = RPA_CONTRACT_PATH.read_text(encoding="utf-8")
        self.assertIn("## TOP 素材批量运行", skill)
        batch_start = skill.index("## TOP 素材批量运行")
        export_start = skill.index(
            "python scripts/export_top_materials.py", batch_start
        )
        rpa_start = skill.index("python scripts/extract_peak_frames.py", batch_start)

        self.assertLess(export_start, rpa_start)
        self.assertIn("候选池", skill)
        self.assertIn("候选上限，不是 RPA 调用数量承诺", skill)
        self.assertIn("成功即后处理", skill)
        self.assertIn("--target-top-n 9", skill)
        self.assertIn("--candidate-top-n 27", skill)
        self.assertIn("--rpa-concurrency 3", skill)
        self.assertIn("--postprocess-concurrency 3", skill)
        self.assertIn("资产表获取素材 ID 数量", skill)
        self.assertIn("传给 RPA 执行的素材 ID 数量", skill)
        self.assertIn("资产表阶段最多获取 `--candidate-top-n` 个素材 ID", skill)
        self.assertIn("不会一次性把候选池全部提交给 RPA", skill)
        self.assertIn("RPA 实际提交数量以 `submittedMaterials` 为准", skill)
        self.assertIn("全成功时实际提交数量等于 `--target-top-n`", skill)
        self.assertIn(
            "有失败时才继续补位提交后续候选，最多不超过 `--candidate-top-n`",
            skill,
        )
        self.assertIn("assetSelectedMaterialCount", contract)
        self.assertIn("rpaSubmittedMaterialCount", contract)
        self.assertIn("top_materials_rpa_ready.json", skill)
        self.assertIn("submittedMaterials", contract)
        self.assertIn("selectedMaterials", contract)
        self.assertIn("rejectedMaterials", contract)
        self.assertIn("cancelledMaterials", contract)
        self.assertIn("top_n_already_selected", contract)

    def test_batch_workflow_requires_advertiser_id_for_asset_selection(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("--advertiser-id", skill)
        self.assertIn("广告主 id", skill)
        self.assertIn("filters.advertiserId", skill)
        self.assertIn("adv_id", skill)
        self.assertIn("COLUMN_NOT_EXIST", skill)
        self.assertIn("不要把资产表过滤字段写成 `advertiser_id`", skill)

    def test_recent_ranges_use_t_plus_two_data_window(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        contract = RPA_CONTRACT_PATH.read_text(encoding="utf-8")

        self.assertIn("T+2 数据回流口径", skill)
        self.assertIn("最新可用完整自然日固定为执行日前第 2 天", skill)
        self.assertIn("近七天", skill)
        self.assertIn("执行日前第 8 天至执行日前第 2 天", skill)
        self.assertIn("2026-07-19", skill)
        self.assertIn("2026-07-25", skill)
        self.assertIn("近三十天", skill)
        self.assertIn("执行日前第 31 天至执行日前第 2 天", skill)
        self.assertIn("2026-06-26", skill)
        self.assertIn("不得将执行任务当天或执行日前第 1 天传入 RPA", skill)
        self.assertIn("自然语言近七天、近三十天必须换算为 CUSTOM", contract)

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

        self.assertIn("--rpa-concurrency 3", skill)
        self.assertIn("--postprocess-concurrency 3", skill)
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

    def test_final_report_requires_in_task_visual_analysis_and_v2_output(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        prompt = DIAGNOSTIC_PROMPT_PATH.read_text(encoding="utf-8")

        self.assertIn("当前执行技能的模型", skill)
        self.assertIn("实际查看", skill)
        self.assertIn("--peak-output-dir", skill)
        self.assertIn("--analysis-json", skill)
        self.assertIn('"schemaVersion": 2', prompt)
        for metric in ("SHOW_COUNT", "CLICK_COUNT", "TRANSACTION_AMOUNT", "JPEG"):
            self.assertIn(metric, prompt)

    def test_final_report_analysis_excludes_execution_process_narrative(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        prompt = DIAGNOSTIC_PROMPT_PATH.read_text(encoding="utf-8")

        self.assertIn("只分析 RPA 提取到的业务事实", prompt)
        self.assertIn("不得分析执行过程", prompt)
        self.assertIn("补位", prompt)
        self.assertIn("verify", prompt)
        self.assertIn("rpa_request_failed", prompt)
        self.assertIn("只分析 RPA 提取到的业务事实", skill)
        self.assertIn("草稿不是完成的商业分析报告", skill)


if __name__ == "__main__":
    unittest.main()
