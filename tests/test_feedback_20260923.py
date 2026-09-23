"""Offline regressions from the September 23 execution evidence."""
import gc
import json
from pathlib import Path
import sys
import weakref

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import batch_runtime
import final_report_lib as report
import run_standard_analysis as cli
import standard_workflow as workflow
import workflow_status
import analysis_handoff as handoff
import test_analysis_reader as reader_fixtures
import test_standard_workflow as workflow_fixtures
from test_standard_workflow import write_rendered_report


def seed_run(root, status='complete'):
    candidate = root / 'analysis.json'
    candidate.write_text('{}')
    workflow.write_run_summary(root / 'run-summary.json', {
        'status': status, 'request': workflow_fixtures.StandardWorkflowTests().request_payload(),
        'artifacts': {}, 'failure': None})
    return candidate


def test_standard_excel_enabled_and_explicit_opt_out():
    args = ['finalize', '--output-dir', 'run', '--analysis-json', 'ai.json']
    assert cli.build_parser().parse_args(args).include_workbook is True
    assert cli.build_parser().parse_args(args + ['--skip-workbook']).include_workbook is False


def test_complete_run_can_add_workbook(tmp_path):
    candidate = seed_run(tmp_path)
    def render(**kwargs):
        assert kwargs['include_workbook'] is True
        result = write_rendered_report(tmp_path)
        (tmp_path / 'final-report/highlight-frames.xlsx').write_bytes(b'workbook')
        return result
    result = workflow.finalize_standard_run(tmp_path, candidate, include_workbook=True,
        render_report=render, mark_ready=lambda *_: True)
    assert result['status'] == 'complete'
    assert result['artifacts']['highlightExport'].endswith('highlight-frames.xlsx')


def test_failed_completed_run_precheck_preserves_delivered_summary(tmp_path):
    candidate = seed_run(tmp_path)
    before = (tmp_path / 'run-summary.json').read_bytes()
    candidate.unlink()
    with pytest.raises(workflow.StandardWorkflowError):
        workflow.finalize_standard_run(tmp_path, candidate)
    assert (tmp_path / 'run-summary.json').read_bytes() == before


def test_render_object_released_before_delivery_validation(tmp_path):
    candidate = seed_run(tmp_path, 'analysis_pending')
    refs = []
    class ReportData(dict):
        pass
    def render(**kwargs):
        data_path, html_path, _ = write_rendered_report(tmp_path)
        data = ReportData(analysisQuality={'contentReviewStatus': 'reviewed'})
        refs.append(weakref.ref(data))
        return data_path, html_path, data
    def mark(*args):
        gc.collect()
        assert refs[0]() is None, 'large report object retained across disk revalidation'
        return True
    result = workflow.finalize_standard_run(tmp_path, candidate, render_report=render, mark_ready=mark)
    assert result['analysisQuality']['contentReviewStatus'] == 'reviewed'


def test_json_writer_streams_without_building_whole_serialized_string(tmp_path, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError('whole-document json.dumps')
    monkeypatch.setattr(batch_runtime.json, 'dumps', forbid)
    target = tmp_path / 'data.json'
    batch_runtime.write_json_atomically(target, {'中文': list(range(1000))})
    assert json.loads(target.read_text())['中文'][-1] == 999


def test_add_excel_reuses_data_but_refreshes_html_and_archive_receipt(tmp_path, monkeypatch):
    top = tmp_path / 'top.json'
    top.write_text('{}')
    peak, records, out = [tmp_path / name for name in ('peak', 'records', 'report')]
    peak.mkdir(); records.mkdir()
    monkeypatch.setattr(report, '_prepare_report_data', lambda *a: ({'materials': [], 'displayFacts': {}}, None))
    monkeypatch.setattr('qianchuan_presentation.build_presentation_report', lambda *a: {'chapters': {}})
    monkeypatch.setattr(report, 'render_html_report', lambda data, *a, **k: data.get('highlightExportHref', 'hidden'))
    report.generate_final_report(top, peak, records, out, include_workbook=False)
    receipt = json.loads((out / 'report-build-receipt.json').read_text())
    report.mark_report_build_stage(out, receipt['inputSha256'], 'archive', artifact='old.zip')
    def forbid(*args):
        raise AssertionError('adding Excel must not rebuild unchanged facts')
    monkeypatch.setattr(report, '_prepare_report_data', forbid)
    def export(data, presentation, target):
        target.write_bytes(b'xlsx')
        return target
    monkeypatch.setattr(report, 'export_highlight_workbook', export)
    report.generate_final_report(top, peak, records, out, include_workbook=True)
    assert (out / 'final-report.html').read_text() == 'highlight-frames.xlsx'
    receipt = json.loads((out / 'report-build-receipt.json').read_text())
    assert 'archive' not in receipt['stages']
    # A deleted workbook is regenerated, even when the previous stage was complete.
    (out / 'highlight-frames.xlsx').unlink()
    report.generate_final_report(top, peak, records, out, include_workbook=True)
    assert (out / 'highlight-frames.xlsx').is_file()


def test_wait_stops_as_soon_as_process_is_interrupted(monkeypatch):
    states = iter([{'nextAction': 'wait'}, {'nextAction': 'resume'}])
    sleeps = []
    monkeypatch.setattr(workflow_status, 'standard_run_status', lambda _: next(states))
    monkeypatch.setattr(workflow_status.time, 'sleep', sleeps.append)
    result = workflow_status.wait_standard_run_status('run', wait_seconds=60)
    assert result['nextAction'] == 'resume'
    assert sum(sleeps) <= 5


def test_preflight_reports_misnested_classification_and_keyword_binding_together(tmp_path):
    root = reader_fixtures.AnalysisReaderTests().inputs(tmp_path)
    part = root / 'bad-chapters.json'
    part.write_text(json.dumps({
        'highlightDashboard': {'groups': [], 'customClassification': {'groups': []}},
        'keywordCloudInsight': {'analysisStatus': 'supported', 'sellingPoints': [
            {'dimension': 'd', 'title': 't', 'description': 'd', 'keywordRefs': ['invented'],
             'evidenceRefs': ['script:invented']} ]}}))
    result = handoff.check_analysis_parts(root, [part])
    messages = str(result['errors'])
    assert 'top-level' in messages
    assert 'keyword-local' in messages


def test_corrupt_frame_export_fails_instead_of_silently_hiding_button(tmp_path):
    from highlight_export import export_highlight_workbook, HighlightExportError
    frame = tmp_path / 'broken.jpg'
    frame.write_bytes(b'not an image')
    data = {'materials': [{'materialId': 'm1', 'highlightFrames': [
        {'framePath': str(frame), 'rankType': 'zero_second'}]}]}
    with pytest.raises(HighlightExportError, match='decode'):
        export_highlight_workbook(data, {}, tmp_path / 'highlight-frames.xlsx')


def test_delivery_checks_excel_target(tmp_path):
    data = {'materials': [], 'highlightExportHref': 'highlight-frames.xlsx'}
    assert batch_runtime._embedded_assets_exist(data, tmp_path) is False
    (tmp_path / 'highlight-frames.xlsx').write_bytes(b'xlsx')
    assert batch_runtime._embedded_assets_exist(data, tmp_path) is True
    assert batch_runtime._embedded_assets_exist({**data, 'highlightExportHref': '../outside.xlsx'}, tmp_path) is False


def test_complete_run_validation_still_requires_explicit_resume(tmp_path):
    candidate = seed_run(tmp_path)
    with pytest.raises(workflow.StandardWorkflowError, match='not awaiting'):
        workflow.validate_analysis(tmp_path, candidate, validate=lambda **k: {})
    result = workflow.validate_analysis(tmp_path, candidate,
        allow_report_resume=True, validate=lambda **k: {})
    assert result['analysisStatus'] == 'valid'
