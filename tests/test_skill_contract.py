from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "SKILL.md"
RPA_CONTRACT_PATH = ROOT / "references" / "rpa-output-contract.md"
DIAGNOSTIC_PROMPT_PATH = ROOT / "prompts" / "diagnostic-analysis-prompt-v1.md"


class BatchSkillContractTests(unittest.TestCase):
    def test_legacy_commercial_report_scripts_are_removed(self):
        self.assertFalse(
            (ROOT / "scripts" / "commercial_report_lib.py").exists()
        )
        self.assertFalse(
            (ROOT / "scripts" / "generate_commercial_report.py").exists()
        )

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

    def test_top18_prompt_does_not_stop_at_54_candidate_export(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertIn("用户要求 Top18", skill)
        self.assertIn("--top-n 54", skill)
        self.assertIn("--target-top-n 18", skill)
        self.assertIn("--candidate-top-n 54", skill)
        self.assertIn("仅完成 `top_materials.json` 导出不代表已经分析 18 个素材", skill)
        self.assertIn("不得把 `top_materials.json` 传给诊断事实包或最终报告", skill)
        self.assertIn("peak-frames/top_materials_rpa_ready.json", skill)

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
        self.assertIn("兼容保留的 `diagnosis-csv` 目录", skill)
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

    def test_final_report_non_ai_steps_are_scripted(self):
        skill = SKILL_PATH.read_text(encoding="utf-8")
        contract = RPA_CONTRACT_PATH.read_text(encoding="utf-8")
        prompt = DIAGNOSTIC_PROMPT_PATH.read_text(encoding="utf-8")

        self.assertIn("--copy-assets", skill)
        self.assertIn("默认不要使用 `--embed-assets`", skill)
        self.assertIn("自动抽取 `videoUrl`", skill)
        self.assertIn("允许用外链播放器展示视频", skill)
        self.assertIn('preload="none"', skill)
        self.assertIn("不得下载后转成 base64", skill)
        self.assertIn("禁止绕过校验直接改 HTML", skill)
        self.assertIn("prepare_diagnostic_analysis.py` injects `videoUrl`", contract)
        self.assertIn('external `<video>` preview with `preload="none"`', contract)
        self.assertIn("video bytes must never be embedded", contract)
        self.assertIn("render_final_report.py --copy-assets", contract)
        self.assertIn("Use `--embed-assets` only for small one-file debug reports", contract)
        self.assertIn("临时签名 URL", prompt)
        self.assertIn("不得在任何分析字段中引用", prompt)
        self.assertIn("不得输出 `batchInsights.topic=integrated`", skill)

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

    def test_final_report_prompt_matches_five_rendered_sections_with_next_action_cards(self):
        prompt = DIAGNOSTIC_PROMPT_PATH.read_text(encoding="utf-8")

        for section in (
            "01｜TOP 素材组合结论",
            "02｜人群洞察",
            "03｜脚本洞察",
            "04｜单素材深度分析",
            "05｜下一轮策略",
        ):
            self.assertIn(section, prompt)
        self.assertNotIn("04｜高光帧洞察", prompt)
        self.assertNotIn('"topic": "highlight"', prompt)
        self.assertNotIn("highlight` 对应 `04", prompt)
        self.assertIn("最终 HTML 页面保留以下 5 个 section", prompt)
        self.assertNotIn("最终 HTML 页面只保留以下 4 个 section", prompt)
        self.assertNotIn('"topic": "integrated"', prompt)
        self.assertIn("不得输出 `batchInsights.topic=integrated`", prompt)
        self.assertIn("只使用 `overallConclusion`", prompt)
        self.assertIn('`nextActions` 对应 `05｜下一轮策略`', prompt)
        self.assertIn('"nextActions":', prompt)
        self.assertIn("策略卡片只展示 title 和 body", prompt)
        self.assertIn("不展示 priority", prompt)
        self.assertNotIn("页面仅保留标题占位", prompt)
        self.assertNotIn("不得输出顶层 `nextActions`", prompt)
        self.assertNotIn("事实包有 warnings", prompt)
        self.assertNotIn('"warnings": []', prompt)

    def test_final_report_prompt_requires_data_rich_secondary_summary(self):
        prompt = DIAGNOSTIC_PROMPT_PATH.read_text(encoding="utf-8")
        skill = SKILL_PATH.read_text(encoding="utf-8")

        self.assertNotIn("真实数据 + 一句总结结论", prompt)
        self.assertNotIn("真实数据 + 一句总结结论", skill)
        self.assertIn("少量关键事实锚点 + 多句分析判断", prompt)
        self.assertIn("少量关键事实锚点 + 多句分析判断", skill)
        self.assertIn("overallConclusion 必须综合人群、脚本、高光帧", prompt)
        self.assertIn("整体结论的数据陈述只作为判断依据，不作为段落主体", prompt)
        self.assertIn("展现次数", prompt)
        self.assertIn("点击次数", prompt)
        self.assertIn("成交金额", prompt)
        self.assertIn("人群洞察必须分别从展现次数、点击次数、成交金额三个角度展开", prompt)
        self.assertIn("优先输出组合公式、机会判断、风险边界和下一轮验证方向", prompt)
        self.assertIn("每条 batchInsights.conclusion 建议 180-280 个中文字符", prompt)

    def test_final_report_prompt_requires_every_highlight_frame_analysis(self):
        prompt = DIAGNOSTIC_PROMPT_PATH.read_text(encoding="utf-8")

        self.assertIn("每一张高光帧都必须对应一条 `highlightAnalyses`", prompt)
        self.assertIn(
            "`highlightAnalyses[*].frameRef` 集合必须与该素材 `frames[*].evidenceRef` 集合完全一致",
            prompt,
        )
        self.assertIn("不得只分析 zero_second 和 peak_top_1", prompt)
        self.assertIn("缺失任何一帧都不能视为完成", prompt)
        self.assertIn("peak_top_2", prompt)
        self.assertIn("peak_top_3", prompt)


if __name__ == "__main__":
    unittest.main()
