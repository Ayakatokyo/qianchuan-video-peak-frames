"""Log-derived regressions for bounded reading and early analysis feedback."""
import copy
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import analysis_handoff as handoff
import analysis_tasks as tasks
import frame_review
import run_standard_analysis as cli
from analysis_task_io import restore_task_pages
import test_analysis_reader as reader_fixtures
from test_frame_review import inputs as frame_inputs


def test_reading_and_frame_review_do_not_import_keyword_segmenter():
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    result = subprocess.run([sys.executable, '-c',
        'import sys; sys.path.insert(0, sys.argv[1]); import frame_review, analysis_tasks; '
        'assert not any(x == "jieba" or x.startswith("jieba.") for x in sys.modules)', str(scripts)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_export_reads_snapshot_once_and_preserves_bounded_pages(tmp_path, monkeypatch):
    root = reader_fixtures.AnalysisReaderTests().inputs(tmp_path)
    original = tasks._snapshot
    snapshots = []
    def snapshot(*args, **kwargs):
        snapshots.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(tasks, '_snapshot', snapshot)
    destination = root / 'task-pages'
    receipt = tasks.export_tasks(root, destination, section='facts')
    assert len(snapshots) == 1
    index = handoff.read_object(receipt['indexPath'])
    assert index['contentReviewed'] is False
    assert len(index['tasks']) == 5  # Four chapters and one material.
    for task in index['tasks']:
        pages = []
        for page in task['pages']:
            raw = (destination / page['path']).read_bytes()
            assert len(raw) <= 8000
            assert hashlib.sha256(raw).hexdigest() == page['sha256']
            payload = json.loads(raw)
            assert payload['inputSha256'] == index['inputSha256']
            pages.append(payload['records'])
        packet = restore_task_pages(pages)
        assert packet['taskId'] == task['taskId']
        expected = tasks._packets(root, task_id=task['taskId'], section='facts')[2][0][2]
        assert packet == expected
    with pytest.raises(ValueError, match='new'):
        tasks.export_tasks(root, destination, section='facts')
    (root / 'diagnostic-analysis-prompt.md').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        tasks.export_tasks(root, root / 'stale', input_sha256=index['inputSha256'])
    assert not (root / 'stale').exists()


def script_part(ref):
    return {'batchInsights': [{'topic': 'script', 'title': title, 'conclusion': '有证据的分析',
                              'evidenceRefs': [ref]} for title in ('一二级内容公式', '脚本文案')]}


def test_script_text_reference_is_rejected_early_but_remains_keyword_evidence(tmp_path):
    root = reader_fixtures.AnalysisReaderTests().inputs(tmp_path)
    source = handoff.read_object(root / 'analysis-units/chapter03-script-input.json')
    # Add a text reference to the keyword cloud and update its source manifest.
    text_ref = 'script:m-1:text:关键词'
    source['keywordCloud'] = {'keywords': [{'keyword': '关键词', 'evidenceRefs': [text_ref]}]}
    path = root / 'analysis-units/chapter03-script-input.json'
    path.write_text(json.dumps(source, ensure_ascii=False))
    manifest_path = root / 'analysis-units/analysis-unit-manifest.json'
    manifest = handoff.read_object(manifest_path)
    entry = next(x for x in manifest['units'] if x['unitId'] == 'chapter03-script')
    entry.update(byteSize=path.stat().st_size, sha256=handoff.file_hash(path))
    manifest_path.write_text(json.dumps(manifest))
    part = root / 'chapter03.json'
    part.write_text(json.dumps(script_part(text_ref)))
    with pytest.raises(ValueError, match='batchInsights.*label'):
        handoff.check_analysis_part(root, part)
    packet = tasks._packets(root, task_id='chapter03', section='references')[2][0][2]
    assert text_ref in packet['allowedEvidenceRefs']
    rules = packet['evidenceRules']
    assert text_ref not in rules['batchInsights']['allowedEvidenceRefs']
    label_ref = source['materials'][0]['labels'][0]['evidenceRef']
    assert label_ref in rules['batchInsights']['allowedEvidenceRefs']
    part.write_text(json.dumps(script_part(label_ref)))
    assert handoff.check_analysis_part(root, part)['status'] == 'chapter_structure_checked'


def test_batch_precheck_reports_all_bad_parts_in_one_snapshot(tmp_path, monkeypatch):
    root = reader_fixtures.AnalysisReaderTests().inputs(tmp_path)
    paths = [root / f'bad-{i}.json' for i in range(2)]
    paths[0].write_text(json.dumps(script_part('script:m-1:text:wrong')))
    paths[1].write_text(json.dumps({'customClassification': {'groups': [
        {'frameRefs': ['highlight:unknown:zero_second']}]}}))
    calls = []
    original = handoff.inspect_inputs
    def inspect(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(handoff, 'inspect_inputs', inspect)
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(['check-analysis-part', '--output-dir', str(tmp_path),
                         '--part', str(paths[0]), '--part', str(paths[1])])
    assert code == 2
    result = json.loads(out.getvalue())
    assert len(result['errors']) == 2
    assert len(calls) == 1
    assert result['contentReviewed'] is False


def test_classification_precheck_lists_duplicates_missing_and_unknown_together(tmp_path):
    root = reader_fixtures.AnalysisReaderTests().inputs(tmp_path)
    ref = handoff.inspect_inputs(root)['frames'][0]['frameRef']
    part = root / 'chapter04.json'
    unknown = 'highlight:unknown:zero_second'
    part.write_text(json.dumps({'customClassification': {'groups': [
        {'frameRefs': [unknown, unknown]}], 'unclassifiedFrameRefs': []}}))
    with pytest.raises(ValueError) as exc:
        handoff.check_analysis_part(root, part)
    message = str(exc.value)
    assert 'duplicates' in message and 'missing' in message and 'unknown' in message
    assert ref in message and unknown in message


def test_frame_review_template_uses_frozen_batches_and_empty_cached_observations(tmp_path):
    root = frame_inputs(tmp_path, [[b'a', b'b'], [b'a', b'b']])
    status = frame_review.start_frame_review(root)
    template = copy.deepcopy(status['checkpointTemplate'])
    assert template['frameRefs'] == status['frameRefs']
    assert status['taskId'] == 'material-001'
    before = handoff.file_hash(root / 'frame-review-progress.json')
    with pytest.raises(ValueError):
        frame_review.checkpoint_frame_review(root, template)
    assert handoff.file_hash(root / 'frame-review-progress.json') == before
    for item in template['observations']:
        item['visualObservation'] = '模型实际观察'
    next_batch = frame_review.checkpoint_frame_review(root, template)
    assert next_batch['materialId'] == 'm-2'
    assert next_batch['taskId'] == 'material-002'
    assert next_batch['checkpointTemplate']['observations'] == []
    with pytest.raises(ValueError, match='frozen batch'):
        frame_review.checkpoint_frame_review(root, template)
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(['frame-review', '--output-dir', str(tmp_path)]) == 0
    assert max(len(line.encode()) for line in out.getvalue().splitlines()) < 2000
    frame_review.checkpoint_frame_review(root, next_batch['checkpointTemplate'])
    assert frame_review.review_status(root)['checkpointTemplate'] is None


def test_assembly_cli_keeps_hash_provenance_without_loading_collection_or_rendering(tmp_path, monkeypatch):
    root = reader_fixtures.AnalysisReaderTests().inputs(tmp_path)
    part = root / 'material.json'
    part.write_text(json.dumps({'materialAnalyses': [{'materialId': 'm-1'}]}))
    monkeypatch.setattr(cli, '_load_workflow', lambda: pytest.fail('assembly does not need report/collection imports'))
    monkeypatch.setattr(cli, 'ensure_prepare_dependencies', lambda **kw: pytest.fail('assembly has no optional dependencies'))
    output = root / 'candidate.json'
    assert cli.main(['assemble-analysis', '--output-dir', str(tmp_path), '--part', str(part),
                     '--analysis-json', str(output)]) == 0
    assert handoff.verify_assembly(output, handoff.inspect_inputs(root))
    part.write_text('{}')
    with pytest.raises(ValueError, match='source hash'):
        handoff.verify_assembly(output, handoff.inspect_inputs(root))


def test_batch_precheck_continues_after_material_schema_error(tmp_path):
    root = reader_fixtures.AnalysisReaderTests().inputs(tmp_path)
    part = root / 'incomplete-material.json'
    part.write_text(json.dumps({'materialAnalyses': [{'materialId': 'm-1'}]}))
    chapter = root / 'chapter.json'
    chapter.write_text(json.dumps({'overallConclusion': '真实结论'}))
    result = handoff.check_analysis_parts(root, [part, chapter])
    assert len(result['errors']) == 1
    assert len(result['parts']) == 1


def test_classification_precheck_accepts_real_coverage_and_rejects_old_manifest(tmp_path):
    root = reader_fixtures.AnalysisReaderTests().inputs(tmp_path)
    inputs = handoff.inspect_inputs(root)
    refs = [f['frameRef'] for f in inputs['frames']]
    point = {'title': '可见信息', 'body': '基于真实画面判断', 'evidenceRefs': refs}
    group = {'frameRefs': refs, 'commonConclusion': [point], 'derivativeDirections': [point]}
    value = {'highlightDashboard': {'groups': [{**group, 'category': 'product'}]},
             'customClassification': {'ruleId': 'test', 'ruleName': '本批规则', 'ruleDescription': '实际画面',
                 'ruleVersion': 'v1', 'inputFrameManifestHash': 'sha256:' + inputs['manifestSha256'],
                 'groups': [{**group, 'groupId': 'bottle', 'groupName': '瓶身展示', 'description': '瓶身可见'}],
                 'unclassifiedFrameRefs': []}}
    path = root / 'chapter04.json'
    path.write_text(json.dumps(value))
    assert handoff.check_analysis_part(root, path)['contentReviewed'] is False
    value['customClassification']['inputFrameManifestHash'] = 'sha256:old'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='ManifestHash mismatch'):
        handoff.check_analysis_part(root, path)
