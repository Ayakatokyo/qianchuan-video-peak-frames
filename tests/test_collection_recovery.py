"""Recovery must preserve the collection barrier and reuse local artifacts."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import repair_manifest
import rpa_batch_runner
import standard_workflow
import workflow_status
import test_rpa_batch_runner as runner_tests


def retained_run(root, *, target=30, collected=24):
    peak = root / 'peak-frames'
    peak.mkdir(parents=True)
    records = root / 'diagnosis-records'
    records.mkdir()
    entries = []
    for i in range(target):
        mid = f'm-{i + 1}'
        entry = rpa_batch_runner.wave_entry({'materialId': mid}, i + 1, lambda: {})
        if i < collected:
            path = records / f'{mid}.json'
            path.write_text(json.dumps([{'recordType': 'material_info', 'materialId': mid,
                                        'videoUrl': f'https://video.example/{mid}.mp4'}]))
            entry.update(rpaStatus='selected', recordsPersisted=True, rpaCsvVerified=True,
                         rpaResultStatus='available', diagnosisRecordsPath=f'diagnosis-records/{mid}.json',
                         diagnosisCsvPath=f'diagnosis-csv/{mid}.csv')
        entries.append(entry)
    (peak / 'batch-manifest.json').write_text(json.dumps({
        'targetTopN': target, 'materials': entries,
        'stageCounts': {'rpaAcceptedCount': collected, 'recordsPersistedCount': collected}}))
    (root / 'top_materials.json').write_text(json.dumps({'materials': [{'materialId': e['materialId']} for e in entries]}))
    request = standard_workflow.WorkflowRequest('2026-09-02', '2026-09-15', target, 'advertiser', 'shop')
    summary = standard_workflow._summary(request, standard_workflow.workflow_paths(root), status='interrupted')
    standard_workflow.write_run_summary(root / 'run-summary.json', summary)
    return request, peak


@pytest.mark.parametrize('records_only', [True, False])
def test_repair_never_downloads_before_collection_target(tmp_path, records_only):
    _, peak = retained_run(tmp_path)
    calls = []
    receipt = repair_manifest.repair_batch(
        peak, records_only=records_only,
        postprocess=lambda *args: calls.append(args) or {})
    assert calls == []
    assert receipt['stageCounts']['rpaAcceptedCount'] == 24
    assert receipt['postprocessAttemptedCount'] == 0
    assert receipt['postprocessRemainingCount'] == 24
    assert receipt['rpaCalls'] == 0
    assert receipt['postprocessBlockedReason'] == ('records_only' if records_only else 'rpa_collection_incomplete')


def test_records_only_does_not_process_even_a_complete_collection(tmp_path):
    _, peak = retained_run(tmp_path, target=2, collected=2)
    receipt = repair_manifest.repair_batch(peak, records_only=True,
        postprocess=lambda *args: pytest.fail('records reconciliation must not download'))
    assert receipt['postprocessAttemptedCount'] == 0
    assert receipt['postprocessRemainingCount'] == 2


@pytest.mark.parametrize('stored_path', ['run_relative', 'cwd_relative', 'absolute', 'repeated_prefix'])
def test_relative_repair_is_idempotent_and_never_repeats_completed_video_work(tmp_path, monkeypatch, stored_path):
    monkeypatch.chdir(tmp_path)
    root = Path('runs/local')
    _, peak = retained_run(root, target=1, collected=1)
    output = peak / 'm-1'
    output.mkdir()
    (output / 'summary.json').write_text(json.dumps({'materialId': 'm-1', 'frames': [{'framePath': 'peak.jpg'}], 'warnings': []}))
    (output / 'summary.md').write_text('summary')
    (output / 'peak.jpg').write_bytes(b'\xff\xd8frame\xff\xd9')
    manifest = json.loads((peak / 'batch-manifest.json').read_text())
    for key, path in [('summaryJson', output / 'summary.json'), ('summaryMd', output / 'summary.md')]:
        manifest['materials'][0][key] = str(path.relative_to(root) if stored_path == 'run_relative'
                                          else path.resolve() if stored_path == 'absolute'
                                          else root / root / path if stored_path == 'repeated_prefix' else path)
    (peak / 'batch-manifest.json').write_text(json.dumps(manifest))
    paths = []
    for _ in range(2):
        receipt = repair_manifest.repair_batch(peak,
            postprocess=lambda *args: pytest.fail('valid summary/JPEG must be reused'))
        entry = json.loads((peak / 'batch-manifest.json').read_text())['materials'][0]
        assert receipt['valid']
        assert receipt['postprocessRemainingCount'] == 0
        assert receipt['stageCounts']['postprocessSucceededCount'] == 1
        paths.append(entry['summaryJson'])
    assert paths == [str((output / 'summary.json').resolve())] * 2


def test_standard_resume_reconciles_once_then_collects_24_to_30_before_postprocess(tmp_path, monkeypatch):
    request, peak = retained_run(tmp_path)
    monkeypatch.setattr(repair_manifest.pipeline, '_attempt_wave_postprocess',
                        lambda *args: pytest.fail('standard resume must not run local repair postprocess'))
    monkeypatch.setattr(standard_workflow, 'validate_ready_materials', lambda *args: None)
    monkeypatch.setattr(standard_workflow, 'write_top_material_roi_facts', lambda *args: None)
    repairs, events = [], []
    original_repair = repair_manifest.repair_batch

    def repair(*args, **kwargs):
        repairs.append(kwargs)
        return original_repair(*args, **kwargs)

    def extract(argv):
        if '--verify' in argv:
            return 0
        runner = runner_tests.WaveBatchRunnerTests()
        runner.setUp()
        saved = json.loads((peak / 'batch-manifest.json').read_text())['materials']
        completed = set()

        def resume_state(args, candidates, entries):
            selected = {}
            for i, item in enumerate(saved[:24]):
                entry = {**entries[i], **item}
                entry['records'] = json.loads((tmp_path / entry['diagnosisRecordsPath']).read_text())
                entries[i] = selected[i] = entry
                completed.add(entry['materialId'])
            return dict(selected), selected, {}, {}, []

        def collect(args, entry):
            assert entry['materialId'] not in completed
            completed.add(entry['materialId'])
            events.append(('rpa', entry['materialId']))
            return runner._verified_rpa_success(entry)

        def postprocess(args, entry):
            assert len(completed) == 30
            events.append(('postprocess', entry['materialId']))
            return runner._success_postprocess(args, entry)

        _, failed, _ = runner.run_batch([f'm-{i + 1}' for i in range(30)], collect, postprocess,
            args={'target_top_n': 30}, resume_state=resume_state)
        return 5 if failed else 0

    result = standard_workflow.resume_standard_run(request, tmp_path, extract_main=extract,
        repair_batch=repair, prepare_facts=lambda **kwargs: (tmp_path / 'diagnostic-analysis/prompt.md', {}))
    assert result['status'] == 'analysis_pending'
    assert len(repairs) == 1
    assert repairs[0]['records_only'] is True
    assert [e[0] for e in events[:6]] == ['rpa'] * 6
    assert len([e for e in events if e[0] == 'postprocess']) == 30


def test_exhausted_collection_does_not_start_partial_postprocess():
    runner = runner_tests.WaveBatchRunnerTests()
    runner.setUp()
    calls = []
    manifest, failed, _ = runner.run_batch(['m-1', 'm-2'],
        lambda args, entry: runner._verified_rpa_success(entry),
        lambda *args: calls.append(args) or runner._success_postprocess(*args), args={'target_top_n': 3})
    assert failed
    assert calls == []
    assert manifest['rpaCollection']['acceptedCount'] == 2


@pytest.mark.parametrize('active,collected,expected_action,expected_stage', [
    (False, 24, 'resume', 'rpa_collection'),
    (False, 30, 'resume', 'local_postprocess'),
    (True, 24, 'wait', 'rpa_collection'),
])
def test_status_distinguishes_waiting_from_collection_recovery(tmp_path, monkeypatch,
        active, collected, expected_action, expected_stage):
    retained_run(tmp_path, collected=collected)
    summary_path = tmp_path / 'run-summary.json'
    summary = json.loads(summary_path.read_text())
    summary['status'] = 'running'
    summary_path.write_text(json.dumps(summary))
    if active:
        monkeypatch.setattr(workflow_status, 'run_lock_state', lambda path: ('active', {'pid': 123}))
    before = summary_path.read_bytes()
    status = workflow_status.standard_run_status(tmp_path)
    assert status['nextAction'] == expected_action
    assert status['recoveryStage'] == expected_stage
    assert status['targetTopN'] == 30
    assert status['rpaRemainingCount'] == 30 - collected
    assert summary_path.read_bytes() == before


@pytest.mark.parametrize('stage,processed,action,recovery', [
    ('recovery', 30, 'resume', 'verification'),
    ('facts', 30, 'resume', 'facts'),
    ('report', 30, 'finalize', 'report'),
])
def test_status_does_not_send_finished_postprocess_back_to_video_work(tmp_path,
        stage, processed, action, recovery):
    _, peak = retained_run(tmp_path, collected=30)
    summary_path = tmp_path / 'run-summary.json'
    summary = json.loads(summary_path.read_text())
    summary.update(status='running', activeStage=stage)
    summary_path.write_text(json.dumps(summary))
    manifest_path = peak / 'batch-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['stageCounts']['postprocessSucceededCount'] = processed
    manifest_path.write_text(json.dumps(manifest))
    status = workflow_status.standard_run_status(tmp_path)
    assert status['nextAction'] == action
    assert status['recoveryStage'] == recovery


@pytest.mark.parametrize('target', [None, 0, True, '2'])
def test_repair_unknown_target_cannot_authorize_postprocess(tmp_path, target):
    _, peak = retained_run(tmp_path, target=2, collected=2)
    manifest_path = peak / 'batch-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['targetTopN'] = target
    manifest_path.write_text(json.dumps(manifest))
    receipt = repair_manifest.repair_batch(peak,
        postprocess=lambda *args: pytest.fail('unknown target must fail closed'))
    assert receipt['postprocessBlockedReason'] == 'rpa_collection_incomplete'


def test_repair_cli_distinguishes_records_only_from_incomplete_postprocess(tmp_path, capsys):
    _, peak = retained_run(tmp_path)
    assert repair_manifest.main(['--output-dir', str(peak)]) == 5
    assert 'postprocess_blocked=rpa_collection_incomplete' in capsys.readouterr().out
    assert repair_manifest.main(['--output-dir', str(peak), '--records-only']) == 0
    assert 'postprocess_blocked=records_only' in capsys.readouterr().out
