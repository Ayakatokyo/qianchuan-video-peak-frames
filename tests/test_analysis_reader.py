"""Lossless model reading: full coverage, bounded pages and frozen inputs."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import analysis_handoff as handoff
from analysis_units_lib import publish_analysis_units
from test_analysis_units import example_analysis


class AnalysisReaderTests(unittest.TestCase):
    def reader(self):
        self.assertIsNotNone(importlib.util.find_spec("analysis_reader"),
                             "formal lossless analysis reader is missing")
        import analysis_reader
        return analysis_reader

    def inputs(self, root):
        facts = example_analysis()
        image = root / "frame.jpg"
        image.write_bytes(b"JPEG")
        facts["highlights"]["materials"][0]["frames"][0]["framePath"] = str(image)
        facts["portfolio"] = {"materials": {
            str(i): {"materialId": str(i), "metrics": {"cost": str(i), "roi": 1.0},
                     "name": "中文素材", "missing": None} for i in range(300)}}
        facts['scripts']['keywordCloud'] = {'keywords': [
            {'keyword': '早餐', 'frequency': 2, 'materialIds': ['m-1'],
             'evidenceRefs': ['script:m-1:text:早餐']}]}
        publish_analysis_units(root / "diagnostic-analysis", facts)
        (root / "diagnostic-analysis/diagnostic-analysis-prompt.md").write_text("prompt")
        return root / "diagnostic-analysis"

    def test_tables_preserve_nested_values_order_and_missing_keys(self):
        reader = self.reader()
        samples = [
            [{"bucket": str(i), "value": i, "sourceMaterialIds": ["m-1"]} for i in range(100)],
            {str(i): {"metrics": {"cost": i, "roi": None}, "flag": False} for i in range(100)},
            [{"a": 1}, {"a": 1.0}, {"a": True}, {"a": None}, {}],
            {"$table": {"columns": []}, "$literal": [1], "a/b~c": {"": []}},
            [[], {}, "文字", None, 0, False],
        ]
        for original in samples:
            encoded = reader.encode_tables(original)
            self.assertEqual(json.dumps(reader.decode_tables(encoded), ensure_ascii=False),
                             json.dumps(original, ensure_ascii=False))
        self.assertLess(len(json.dumps(reader.encode_tables(samples[0]))),
                        len(json.dumps(samples[0])) * .6)

    def test_pages_restore_every_fact_including_long_unicode_script(self):
        reader = self.reader()
        original = {"script": "汉字\\\"🙂" * 10000, "rows": [
            {"bucket": str(i), "value": i, "empty": []} for i in range(1000)]}
        pages = reader.paginate(reader.encode_tables(original))
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(reader.json_text(page).encode()), reader.PAGE_BYTES)
        reconstructed = reader.restore_pages(pages)
        self.assertEqual(reader.decode_tables(reconstructed), original)

    def test_metric_names_remain_next_to_their_bucket_tables(self):
        reader = self.reader()
        facts = {'metrics': {metric: {'buckets': [
            {'bucket': str(i), 'value': i, 'sources': ['m-1']} for i in range(100)]}
            for metric in ('SHOW_COUNT', 'CLICK_COUNT', 'TRANSACTION_AMOUNT')}}
        encoded = reader.encode_tables(facts)
        self.assertIn('SHOW_COUNT', encoded['metrics'])
        self.assertEqual(reader.decode_tables(encoded), facts)

    def test_plan_and_read_cover_all_units_without_mutating_source(self):
        reader = self.reader()
        with tempfile.TemporaryDirectory() as directory:
            root = self.inputs(Path(directory))
            before = {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            plan = reader.analysis_plan(root)
            self.assertEqual([x['taskId'] for x in plan['tasks']],
                             ['chapter01', 'chapter02', 'chapter03', 'material-001', 'chapter04'])
            self.assertFalse(plan['contentReviewed'])
            self.assertIn('analysisProvenance', plan['tasks'][0]['outputFields'])
            for task in plan['tasks']:
                pages = []
                for page in range(task['pageCount']):
                    result = reader.read_analysis(root, task['taskId'], page, plan['inputSha256'])
                    self.assertEqual(result['nextPage'], page + 1 if page + 1 < task['pageCount'] else None)
                    self.assertLessEqual(len(reader.json_text(result).encode()), reader.PAGE_BYTES + 2048)
                    pages.append(result['records'])
                original = handoff.read_object(root / 'analysis-units' / task['sourceFile'])
                for key in task['sourcePath']:
                    original = original[key]
                self.assertEqual(reader.decode_tables(reader.restore_pages(pages)), original)
            self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_stale_prompt_image_or_manifest_and_invalid_cursor_fail_closed(self):
        reader = self.reader()
        with tempfile.TemporaryDirectory() as directory:
            root = self.inputs(Path(directory))
            binding = reader.analysis_plan(root)['inputSha256']
            for task, page, sha in [('bad', 0, binding), ('chapter01', -1, binding),
                                    ('chapter01', 99999, binding), ('chapter01', 0, 'stale')]:
                with self.assertRaises(ValueError):
                    reader.read_analysis(root, task, page, sha)
            for path in [root / 'diagnostic-analysis-prompt.md', root.parent / 'frame.jpg']:
                old = path.read_bytes()
                path.write_bytes(old + b'changed')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    reader.read_analysis(root, 'chapter01', 0, binding)
                path.write_bytes(old)
            (root / 'analysis-units/chapter01-portfolio-input.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                reader.read_analysis(root, 'chapter01', 0, binding)

    def test_cli_reads_bounded_page_without_network_or_fact_writes(self):
        import run_standard_analysis as cli
        with tempfile.TemporaryDirectory() as directory:
            root = self.inputs(Path(directory))
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli.main(['analysis-plan', '--output-dir', str(root.parent), '--verify'])
            self.assertEqual(code, 0)
            plan = json.loads(output.getvalue())
            output = io.StringIO()
            with redirect_stdout(output):
                code = cli.main(['read-analysis', '--output-dir', str(root.parent), '--task', 'chapter01',
                                 '--input-sha256', plan['inputSha256']])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['page'], 0)

    def test_material_precheck_rejects_missing_frames_refs_and_invalid_decisions(self):
        self.assertTrue(hasattr(handoff, 'check_material_part'), 'material precheck is missing')
        with tempfile.TemporaryDirectory() as directory:
            root = self.inputs(Path(directory))
            inputs = handoff.inspect_inputs(root)
            ref = inputs['frames'][0]['frameRef']
            part = {'materialAnalyses': [{'materialId': 'm-1', 'audienceConclusion': '人群证据有限。',
                'scriptConclusion': '脚本强调早餐场景。', 'interactionConclusion': '互动数据不足。',
                'integratedConclusion': '先验证早餐场景。', 'nextAction': '对照早餐表达。',
                'decision': 'hold', 'frameCoverageNote': '', 'evidenceRefs': [ref],
                'highlightAnalyses': [{'frameRef': ref, 'visualObservation': '图片测试观察。',
                                       'commercialInterpretation': '测试解释。'}]}]}
            path = root / 'part.json'
            path.write_text(json.dumps(part))
            result = handoff.check_material_part(root, path)
            self.assertFalse(result['contentReviewed'])
            self.assertTrue(result['finalValidationRequired'])
            for field, value in [('highlightAnalyses', []), ('decision', 'expand'),
                                 ('evidenceRefs', ['script:other:unknown'])]:
                broken = copy.deepcopy(part)
                broken['materialAnalyses'][0][field] = value
                path.write_text(json.dumps(broken))
                with self.assertRaises(ValueError):
                    handoff.check_material_part(root, path)


if __name__ == '__main__':
    unittest.main()
