from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "SKILL.md"
RUNBOOK_PATH = ROOT / "references" / "qianchuan-operations-runbook.md"
RPA_CONTRACT_PATH = ROOT / "references" / "rpa-output-contract.md"
DIAGNOSTIC_PROMPT_PATH = ROOT / "prompts" / "diagnostic-analysis-prompt-v1.md"
OPENAI_CONFIG_PATH = ROOT / "agents" / "openai.yaml"
FACTS_CLI_PATH = ROOT / "scripts" / "prepare_diagnostic_analysis.py"
REPORT_CLI_PATH = ROOT / "scripts" / "render_final_report.py"
ACTIVE_OPERATIONAL_DOCUMENTS = (
    SKILL_PATH,
    RUNBOOK_PATH,
    RPA_CONTRACT_PATH,
    ROOT / "docs" / "skill-flow.md",
)


class StandardWorkflowSkillContractTests(unittest.TestCase):
    def test_entry_prompt_requests_the_three_business_conditions(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("name: 千川短视频素材分析", skill)
        self.assertIn("分析周期", skill)
        self.assertIn("Top素材数量", skill)
        self.assertIn("广告主id", skill)
        self.assertIn("分析周期是 ____", skill)
        self.assertIn("Top素材数量是 ____", skill)
        self.assertIn("广告主id是 ____", skill)

    def test_skill_documents_only_the_standard_prepare_and_finalize_commands(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("run_standard_analysis.py prepare", skill)
        self.assertIn("run_standard_analysis.py finalize", skill)
        self.assertNotIn("python scripts/export_top_materials.py", skill)
        self.assertNotIn("python scripts/extract_peak_frames.py", skill)
        self.assertNotIn("repair_manifest.py", skill)
        self.assertNotIn("--rpa-concurrency", skill)
        self.assertNotIn("--resume", skill)

    def test_skill_keeps_t_plus_two_date_conversion(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("T+2", skill)
        self.assertIn("执行日前第 8 天至执行日前第 2 天", skill)
        self.assertIn("执行日前第 31 天至执行日前第 2 天", skill)
        self.assertIn("CUSTOM", skill)

    def test_skill_requires_ai_to_use_the_fact_package_and_actual_jpegs(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

        self.assertIn("diagnostic-analysis-data.json", skill)
        self.assertIn("diagnostic-analysis-prompt.md", skill)
        self.assertIn("实际查看", skill)
        self.assertIn("JPEG", skill)
        self.assertIn("diagnostic-ai-analysis.json", skill)
        self.assertIn("schemaVersion: 5", skill)
        self.assertIn("highlightDashboard", skill)
        self.assertIn("v4 仅用于历史报告兼容", skill)
        self.assertIn("不超过 6 帧", skill)
        self.assertIn("不得跳过任何帧", skill)
        self.assertIn("经营表现、人群、脚本、高光画面和互动时序", skill)
        self.assertIn("图片理解工具首次被策略拒绝", runbook)

    def test_skill_delivers_only_a_complete_standard_report(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("run-summary.json", skill)
        self.assertIn("complete", skill)
        self.assertIn("final-report.zip", skill)
        self.assertIn("final-report/final-report.html", skill)
        self.assertIn("图片不依赖三方链接", skill)
        self.assertIn("视频仍使用平台返回的 URL", skill)
        self.assertIn("不得交付草稿报告", skill)

    def test_skill_documents_finalize_retry_after_correcting_ai_json(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("修正 AI JSON 后，直接重新执行 finalize", skill)
        self.assertIn("无需重新执行 prepare 或任何运行命令", skill)

    def test_skill_requires_complete_ready_data_before_ai(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

        self.assertIn("run_standard_analysis.py status", skill)
        self.assertIn("analysis_pending", skill)
        self.assertIn("diagnostic-analysis-data.json", skill)
        self.assertIn("diagnostic-analysis-prompt.md", skill)
        self.assertIn("不得从 `top_materials.json`", skill)
        self.assertIn("不得手写 `missingRanks`", skill)
        self.assertIn("run_standard_analysis.py status", runbook)

    def test_skill_requires_a_single_ai_handoff_preflight(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("AI 交接前置检查", skill)
        self.assertIn("先读取事实包和提示词", skill)
        self.assertIn("素材数、成功帧数", skill)
        self.assertIn("missingFrames", skill)

    def test_prompt_requires_contract_preflight_before_generation(self):
        prompt = DIAGNOSTIC_PROMPT_PATH.read_text(encoding="utf-8")

        self.assertIn("生成前门禁自检", prompt)
        self.assertIn("decision 只能使用", prompt)
        self.assertIn("每张成功高光帧恰好一条", prompt)
        self.assertIn("final_report_lib.py", prompt)

    def test_skill_uses_generated_prompt_as_ai_json_contract(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("以该提示词为 AI JSON 输出契约", skill)
        self.assertNotIn("HIGHLIGHT_DASHBOARD_MINIMUMS", skill)

    def test_skill_distinguishes_missing_live_progress_from_missing_rpa_data(self):
        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
        contract = RPA_CONTRACT_PATH.read_text(encoding="utf-8")

        self.assertIn("recordsPersistedUnknownCount", contract)
        self.assertIn("不能直接覆盖目录", runbook)
        self.assertIn("running` 或 `submitted", runbook)
        self.assertIn("CSV -> records", runbook)
        self.assertIn("records -> 后处理", runbook)

    def test_skill_treats_guardrail_after_long_submit_as_submission_unknown(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

        self.assertIn("运维手册", skill)
        self.assertIn("submitted_unverified", runbook)
        self.assertIn("needs_confirmation", runbook)
        self.assertIn("不得在未知状态将任务改写为 `failed`", runbook)
        self.assertIn("提交结果未知", runbook)
        self.assertIn("不得在未知状态重提 prepare", runbook)

    def test_skill_documents_the_long_task_completion_contract(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

        self.assertIn("长时执行工具的完成态契约", skill)
        self.assertIn("completion_mode: data_collection", runbook)
        self.assertIn("verify_command", runbook)
        self.assertIn("success_pattern", runbook)
        self.assertIn("不是 `prepare` 的命令行参数", runbook)

    def test_skill_delegates_tool_metadata_to_the_runbook(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("运维手册", skill)
        self.assertNotIn("这些是工具元数据，不是", skill)

    def test_skill_delegates_long_task_metadata_to_the_runbook(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

        self.assertIn("长任务元数据", runbook)
        self.assertNotIn("max_runtime_seconds: 3600", skill)

    def test_guardrail_recovery_limits_rejected_probe_calls_and_preserves_evidence(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

        self.assertIn("连续策略拒绝", runbook)
        self.assertIn("停止同类探查", runbook)
        self.assertIn("最后一次成功观测", runbook)
        self.assertIn("锁文件", runbook)
        self.assertIn("只读核验恢复后", runbook)

    def test_skill_freezes_ai_frame_stage_after_first_guardrail_rejection(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

        self.assertIn("冻结并恢复帧审阅", skill)
        self.assertIn("图片理解工具首次被策略拒绝", runbook)
        self.assertIn("不得继续拆分批次、逐帧重试", runbook)
        self.assertIn("frame_review_pending", runbook)
        self.assertIn("从未完成批次续接", runbook)

    def test_skill_requires_rpa_and_postprocess_completion_before_next_wave(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        contract = RPA_CONTRACT_PATH.read_text(encoding="utf-8")

        self.assertIn("RPA 输出契约", skill)
        self.assertIn("RPA and postprocess terminality", contract)
        self.assertIn("before it opens the next RPA wave", contract)

    def test_runbook_owns_wave_recovery_commands(self):
        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

        self.assertIn("仅在标准工作流失败且需要排障时使用", runbook)
        self.assertIn("repair_manifest.py", runbook)
        self.assertIn("--resume", runbook)
        self.assertIn("不重新调用 RPA", runbook)
        self.assertNotIn("--material-id", runbook)

    def test_active_workflow_does_not_expose_removed_cli_wrappers(self):
        self.assertFalse(FACTS_CLI_PATH.exists())
        self.assertFalse(REPORT_CLI_PATH.exists())
        for document_path in ACTIVE_OPERATIONAL_DOCUMENTS:
            document = document_path.read_text(encoding="utf-8")
            self.assertNotIn("python scripts/prepare_diagnostic_analysis.py", document)
            self.assertNotIn("python scripts/render_final_report.py", document)

        runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
        self.assertIn("extract_peak_frames.py", runbook)
        self.assertIn("repair_manifest.py", runbook)

    def test_prompt_retains_business_analysis_and_evidence_contract(self):
        prompt = DIAGNOSTIC_PROMPT_PATH.read_text(encoding="utf-8")

        for metric in ("SHOW_COUNT", "CLICK_COUNT", "TRANSACTION_AMOUNT"):
            self.assertIn(metric, prompt)
        for section in (
            "01｜TOP 素材组合结论",
            "02｜人群洞察",
            "03｜脚本洞察",
            "04｜高光画面洞察",
            "05｜单素材深度分析",
        ):
            self.assertIn(section, prompt)
        self.assertIn("不得分析执行过程", prompt)
        self.assertIn("每一张高光帧都必须对应一条 `highlightAnalyses`", prompt)
        self.assertIn("成功生成的 JPEG", prompt)
        self.assertIn("frameCoverageNote", prompt)
        self.assertIn("missingFrames", prompt)
        self.assertIn("必须说明哪个预期帧不可用", prompt)
        self.assertIn("明确视觉结论基于其余成功帧", prompt)
        self.assertIn("不得只分析 zero_second 和 peak_top_1", prompt)
        self.assertIn("peak_top_2", prompt)
        self.assertIn("peak_top_5", prompt)
        self.assertIn("highlightDashboard", prompt)
        self.assertIn("不得宣称帧对应具体话术", prompt)
        self.assertNotIn("缺失任何一帧都不能视为完成", prompt)

    def test_rpa_contract_remains_the_source_of_operational_details(self):
        contract = RPA_CONTRACT_PATH.read_text(encoding="utf-8")

        self.assertIn("top_materials_rpa_ready.json", contract)
        self.assertIn("postprocessSucceededCount", contract)
        self.assertIn("CSV -> records", contract)
        self.assertIn("records -> 后处理", contract)
        self.assertIn("run_standard_analysis.py finalize", contract)
        self.assertIn("final-report.zip", contract)
        self.assertIn("Video bytes must never be downloaded", contract)
    def test_rpa_contract_documents_only_wave_top_n_execution(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        contract = RPA_CONTRACT_PATH.read_text(encoding="utf-8")

        for removed_flag in (
            "--preflight-only",
            "--rpa-preflight-json",
            "--allow-rpa-refetch",
            "--no-rpa-wave-barrier",
        ):
            self.assertNotIn(removed_flag, skill)
            self.assertNotIn(removed_flag, contract)

    def test_entry_prompt_matches_report_workflow(self):
        prompt = OPENAI_CONFIG_PATH.read_text(encoding="utf-8")

        self.assertIn('display_name: "千川短视频素材分析"', prompt)
        self.assertIn("$千川短视频素材分析", prompt)
        self.assertIn("分析周期是 ____", prompt)


if __name__ == "__main__":
    unittest.main()
