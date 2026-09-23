"""Concise inputs preserve evidence and cannot authorize incomplete reports."""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_analysis_reader as fixtures
import analysis_handoff as handoff
import run_standard_analysis as cli


def test_portfolio_projection_uses_all_costs_without_changing_metrics():
    from analysis_tasks import model_view
    source = {'portfolio': {'summaryMetrics': {'roi': '1.234'}, 'materialCount': 12,
              'hotMaterialRule': {'qualifyingMaterials': [{'materialId': '9'}]},
              'materials': {str(i): {'materialId': str(i), 'totalCost': '0.10'} for i in range(12)}}}
    original = copy.deepcopy(source)
    view = model_view('chapter01', source)
    assert source == original
    assert view['portfolio']['summaryMetrics'] == {'roi': '1.234'}
    assert view['portfolio']['hotMaterialRule'] == source['portfolio']['hotMaterialRule']
    stats = view['portfolio']['costDistribution']
    assert stats['totalCost'] == '1.20'
    assert stats['top10Cost'] == '1.00'
    assert stats['zeroCostCount'] == 0
    assert stats['sourceCount'] == 12
    assert len(view['portfolio']['topCostMaterials']) == 10
    assert 'materials' not in view['portfolio']
    source['portfolio']['materials']['0']['totalCost'] = None
    view = model_view('chapter01', source)
    assert view['portfolio']['costDistribution']['totalCost'] is None
    assert view['portfolio']['costDistribution']['missingCostCount'] == 1


def test_material_retains_script_frames_and_peaks_but_not_curve_points():
    from analysis_tasks import model_view
    source = {'materialId': '1', 'script': {'text': '原文不可截断'},
              'highlight': {'frames': [{'evidenceRef': 'highlight:1:zero_second'}], 'missingFrames': ['x']},
              'interactionTimeline': {'metrics': [{'metric': 'CLICK_COUNT', 'peak': {'second': 2, 'value': 4},
                                                  'points': [{'second': 0, 'value': 2}, {'second': 2, 'value': 4}]}]},
              'evidenceIndex': {'interaction': ['interaction:CLICK_COUNT:total']}}
    view = model_view('material-001', source)
    assert view['script'] == source['script']
    assert view['highlight'] == source['highlight']
    metric = view['interactionTimeline']['metrics'][0]
    assert metric['peak'] == {'second': 2, 'value': 4}
    assert metric['pointCount'] == 2
    assert 'points' not in metric
    assert len(source['interactionTimeline']['metrics'][0]['points']) == 2


def test_pages_restore_long_unicode_and_are_bounded():
    from analysis_task_io import task_pages, restore_task_pages, pretty_json
    value = {'text': '汉字🙂' * 5000, 'rows': [{'i': i, 'value': None} for i in range(200)]}
    pages = task_pages(value)
    assert restore_task_pages(pages) == value
    for page in pages:
        rendered = pretty_json(page)
        assert len(rendered.encode()) <= 10000
        assert max(len(line.encode()) for line in rendered.splitlines()) <= 1200


def test_task_cli_reports_skeleton_refs_and_rejects_stale_input(tmp_path):
    root = fixtures.AnalysisReaderTests().inputs(tmp_path)
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(['analysis-task', '--output-dir', str(tmp_path)])
    assert code == 0
    plan = json.loads(out.getvalue())
    assert plan['tasks'][0]['taskId'] == 'chapter01'
    assert plan['contentReviewed'] is False
    from analysis_tasks import read_task
    from analysis_task_io import restore_task_pages
    pages = []
    task = next(t for t in plan['tasks'] if t['taskId'] == 'chapter03')
    for page in range(task['pageCount']):
        result = read_task(root, 'chapter03', page, plan['inputSha256'])
        pages.append(result['records'])
    packet = restore_task_pages(pages)
    assert len(packet['outputSkeleton']['batchInsights']) == 2
    assert packet['outputSkeleton']['batchInsights'][0]['conclusion'] is None
    assert packet['scopedFacts']['materials'][0]['text'] == '完整脚本文案'
    (root / 'diagnostic-analysis-prompt.md').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        read_task(root, 'chapter03', 0, plan['inputSha256'])


def test_chapter_precheck_reports_missing_script_slot_and_unknown_refs(tmp_path):
    root = fixtures.AnalysisReaderTests().inputs(tmp_path)
    path = root / 'chapter03.json'
    path.write_text(json.dumps({'batchInsights': [
        {'topic': 'script', 'title': '脚本文案', 'conclusion': '简短结论', 'evidenceRefs': ['script:wrong']}]}))
    with pytest.raises(ValueError, match='2 script'):
        handoff.check_analysis_part(root, path)
    value = json.loads(path.read_text())
    value['batchInsights'] *= 2
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='reference'):
        handoff.check_analysis_part(root, path)


def test_one_evidenced_classification_point_passes_without_fake_limitation():
    from test_highlight_depth import candidate
    import analysis_quality as quality
    findings = quality.highlight_depth_findings(candidate(3, 1))
    assert not [f for f in findings if f['level'] == 'error']
    assert not [f for f in findings if f.get('requiredDecision') == 'evidence_limited']


def test_audience_tail_is_disclosed_without_changing_denominator_or_short_dimensions():
    from analysis_tasks import model_view
    cities = [{'bucket': str(i), 'metricValue': i, 'evidenceRef': f'audience:SHOW_COUNT:city:{i}'} for i in range(100)]
    metrics = {'SHOW_COUNT': {'dimensions': {'city': {'totalMetricValue': 9999, 'buckets': cities},
                                           'gender': {'buckets': [{'bucket': '女'}, {'bucket': '男'}]}}}}
    refs = [row['evidenceRef'] for row in cities]
    source = {'audience': {'metrics': metrics}, 'evidenceIndex': {'audience': refs, 'script': ['script:1:text:原文']}}
    view = model_view('material-001', source)
    city = view['audience']['metrics']['SHOW_COUNT']['dimensions']['city']
    assert city['totalMetricValue'] == 9999
    assert len(city['buckets']) == 5
    assert city['bucketReadingCoverage']['omittedCount'] == 95
    assert view['audience']['metrics']['SHOW_COUNT']['dimensions']['gender'] == metrics['SHOW_COUNT']['dimensions']['gender']
    assert view['evidenceIndex']['audience'] == refs[:5]
    assert view['evidenceIndex']['script'] == ['script:1:text:原文']
    assert len(source['evidenceIndex']['audience']) == 100


def test_null_or_unrecognized_chapter_part_is_not_prechecked(tmp_path):
    root = fixtures.AnalysisReaderTests().inputs(tmp_path)
    path = root / 'part.json'
    for value in ({}, {'unexpected': 'x'}, {'overallConclusion': None}):
        path.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            handoff.check_analysis_part(root, path)


@pytest.mark.parametrize('value', [
    {'batchInsights': []}, {'batchInsights': 'unfinished'},
    {'batchInsights': ['unfinished']},
    {'batchInsights': [{'topic': 'unknown'}]},
    {'overallConclusion': ''}, {'schemaVersion': 4, 'overallConclusion': '结论'},
])
def test_invalid_chapter_shape_fails_precheck_cleanly(tmp_path, value):
    root = fixtures.AnalysisReaderTests().inputs(tmp_path)
    path = root / 'part.json'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        handoff.check_analysis_part(root, path)


def test_task_sections_preserve_facts_and_bound_entire_cli_page(tmp_path):
    from analysis_tasks import task_plan, read_task
    from analysis_task_io import restore_task_pages, pretty_json
    root = fixtures.AnalysisReaderTests().inputs(tmp_path)
    packets = {}
    for section in ('all', 'facts', 'contract', 'references'):
        plan = task_plan(root, section=section)
        task = next(t for t in plan['tasks'] if t['taskId'] == 'chapter03')
        pages = []
        for page in range(task['pageCount']):
            result = read_task(root, 'chapter03', page, plan['inputSha256'], section=section)
            assert len((pretty_json(result) + '\n').encode()) <= 8000
            pages.append(result['records'])
        packets[section] = restore_task_pages(pages)
    assert packets['facts']['scopedFacts'] == packets['all']['scopedFacts']
    assert packets['contract']['outputSkeleton'] == packets['all']['outputSkeleton']
    assert packets['references']['allowedEvidenceRefs'] == packets['all']['allowedEvidenceRefs']
    assert 'allowedEvidenceRefs' not in packets['facts']
    assert 'outputSkeleton' not in packets['facts']
    assert 'scopedFacts' not in packets['contract']
    with pytest.raises(ValueError, match='section'):
        task_plan(root, section='unknown')


def test_cli_facts_projection_has_no_duplicate_reference_table(tmp_path):
    from analysis_tasks import task_plan
    root = fixtures.AnalysisReaderTests().inputs(tmp_path)
    plan = task_plan(root)
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(['analysis-task', '--output-dir', str(tmp_path), '--task', 'chapter03',
                         '--input-sha256', plan['inputSha256'], '--section', 'facts'])
    assert code == 0
    result = json.loads(out.getvalue())
    assert result['section'] == 'facts'
    assert 'allowedEvidenceRefs' not in out.getvalue()
    assert len(out.getvalue().encode()) <= 8000


def test_long_reference_lists_do_not_repeat_a_path_for_every_reference():
    from analysis_task_io import task_pages, restore_task_pages, pretty_json
    value = {'scopedFacts': {'evidenceIndex': {'audience': [
        f'audience:SHOW_COUNT:city:城市-{i}' for i in range(1000)]}}}
    pages = task_pages(value, budget=8000, envelope={'inputSha256': 'a' * 64})
    assert restore_task_pages(pages) == value
    # Envelope and path bookkeeping should not double the actual reference data.
    size = sum(len(pretty_json(p).encode()) for p in pages)
    assert size < len(pretty_json(value).encode()) * 1.3
