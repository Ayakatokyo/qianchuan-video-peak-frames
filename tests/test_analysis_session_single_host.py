"""Offline default single-host work and explicit multi-host compatibility.

These tests verify bounded local work, mode persistence and recovery receipts.
They do not demonstrate host context release, native vision or online acceptance.
"""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import analysis_session as session
import run_standard_analysis as cli
from analysis_handoff import file_hash, inspect_inputs, read_object, verify_assembly
from test_analysis_session import chapter01, full_work, only_materials, submit, write
from test_analysis_session_end_to_end import authored_parts, pages, synthetic_inputs
from test_analysis_session_segments import (
    material_payload, snapshot, synthetic_final_chapter, thirty_material_inputs,
)
from test_frame_review import inputs as frame_inputs


def current(root, **options):
    return full_work(session.advance_session(root, **options))


def assert_single_host(work, *, complete=False):
    assert work['executionMode'] == 'single_host'
    assert work['requiresHostRestart'] is False
    assert work['executionStatus'] == ('complete' if complete else 'active')
    assert work['nextAction'] != 'start_next_segment'
    assert not work.get('segmentId')
    assert not work.get('hostRunId')
    if not complete:
        assert work['nextAction'] == 'continue_current_task'


def test_default_creates_persistent_single_host_mode_without_segment_identity(tmp_path):
    root = synthetic_inputs(tmp_path)
    before = snapshot(root)
    not_started = session.read_session_status(root)
    assert not_started['status'] == 'not_started'
    assert not_started['executionMode'] == 'single_host'
    assert not_started['requiresHostRestart'] is False
    assert snapshot(root) == before
    work = current(root)
    assert work['taskId'] == 'chapter01'
    assert_single_host(work)
    state = read_object(root / 'analysis-session/state.json')
    assert state['executionMode'] == 'single_host'
    assert not state.get('segmentHistory')
    assert current(root) == work
    assert current(root, execution_mode='single_host') == work
    next_work = current(root, submission_json=submit(root, work, chapter01()))
    assert next_work['taskId'] == 'chapter02'
    assert next_work['completedTaskCount'] == 1
    assert_single_host(next_work)


def test_current_material_reads_only_local_pages_and_current_six_image_group(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[f'{number}-{frame}'.encode() for frame in range(6)]
        for number in range(12)])
    only_materials(monkeypatch)
    work = current(root)
    assert_single_host(work)
    assert work['taskCount'] == 12
    assert work['taskId'] == 'material-001'
    facts = pages(work['factsPages'])
    assert facts['taskId'] == 'material-001'
    assert facts['scopedFacts']['materialId'] == 'm-1'
    assert len(work['frameReview']['imagesToView']) == 6
    assert len(work['frameReview']['visionBatches']) == 1
    assert {image['materialId'] for image in work['frameReview']['imagesToView']} == {'m-1'}
    request = read_object(work['frameReview']['fileUnderstandingRequest']['path'])
    assert request['materialId'] == 'm-1'
    assert len(request['files']) == 6
    assert {image['sourceFrameRef'].split(':')[1] for image in request['files']} == {'m-1'}
    assert not (root / 'analysis-session/work/material-002').exists()


def test_single_host_continues_past_three_materials_and_480_seconds(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[bytes([number])] for number in range(5)])
    only_materials(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(session.time, 'time', lambda: clock[0])
    work = current(root)
    for number in range(5):
        assert_single_host(work)
        assert work['taskId'] == f'material-{number + 1:03d}'
        clock[0] += 600
        recovered = current(root)
        assert recovered['taskId'] == work['taskId']
        assert_single_host(recovered)
        work = current(root, submission_json=material_payload(root, recovered))
        assert work['completedTaskCount'] == number + 1
    assert work['status'] == 'ready_for_validation'
    assert_single_host(work, complete=True)
    assert not read_object(root / 'analysis-session/state.json').get('segmentHistory')


@pytest.mark.parametrize('options', [
    {'begin_segment': True, 'segment_id': 'segment-1', 'host_run_id': 'host-1'},
    {'segment_id': 'segment-1'}, {'host_run_id': 'host-1'},
    {'after_segment': 'segment-0'}, {'previous_host_ended': True},
    {'material_limit': 3}, {'soft_time_budget_seconds': 480},
])
def test_explicit_single_host_rejects_segment_and_budget_parameters(tmp_path, options):
    root = synthetic_inputs(tmp_path)
    before = snapshot(root, include_locks=False)
    with pytest.raises(ValueError):
        session.advance_session(root, execution_mode='single_host', **options)
    assert snapshot(root, include_locks=False) == before


@pytest.mark.parametrize('options', [
    {'execution_mode': 'multi_host'},
    {'begin_segment': True, 'segment_id': 'segment-1', 'host_run_id': 'host-1'},
    {'material_limit': 3}, {'soft_time_budget_seconds': 480},
])
def test_existing_single_host_mode_cannot_be_changed_or_gain_implicit_segments(tmp_path, options):
    root = synthetic_inputs(tmp_path)
    current(root)
    before = snapshot(root, include_locks=False)
    with pytest.raises(ValueError):
        session.advance_session(root, **options)
    assert snapshot(root, include_locks=False) == before
    assert read_object(root / 'analysis-session/state.json')['executionMode'] == 'single_host'


@pytest.mark.parametrize('explicit_mode', [None, 'multi_host'])
def test_explicit_begin_selects_multi_host_and_retains_yield_contract(tmp_path, explicit_mode):
    root = synthetic_inputs(tmp_path)
    options = {'execution_mode': explicit_mode} if explicit_mode else {}
    work = current(root, begin_segment=True, segment_id='segment-1', host_run_id='host-1', **options)
    assert work['executionMode'] == 'multi_host'
    assert work['executionStatus'] == 'active'
    yielded = current(root, segment_id='segment-1', host_run_id='host-1',
        submission_json=submit(root, work, chapter01()))
    assert yielded['taskId'] == 'chapter02'
    assert yielded['executionMode'] == 'multi_host'
    assert yielded['executionStatus'] == 'yield_required'
    assert yielded['requiresHostRestart'] is True
    assert yielded['nextAction'] == 'start_next_segment'
    assert not {'factsPages', 'frameReview'} & yielded.keys()
    before = snapshot(root, include_locks=False)
    for options in ({}, {'execution_mode': 'single_host'},
                    {'execution_mode': 'single_host', 'segment_id': 'segment-1', 'host_run_id': 'host-1'}):
        with pytest.raises(ValueError):
            session.advance_session(root, **options)
        assert snapshot(root, include_locks=False) == before


def test_segment_history_without_mode_is_inferred_multi_host_and_cannot_erase_yield(tmp_path):
    root = synthetic_inputs(tmp_path)
    work = current(root, begin_segment=True, segment_id='segment-1', host_run_id='host-1')
    current(root, segment_id='segment-1', host_run_id='host-1',
        submission_json=submit(root, work, chapter01()))
    # Simulate the older state shape under the same implementation binding;
    # this is not migration of an old implementation or its frozen inputs.
    state_path = root / 'analysis-session/state.json'
    state = read_object(state_path)
    state.pop('executionMode')
    write(state_path, state)
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['executionMode'] == 'multi_host'
    assert status['executionStatus'] == 'yield_required'
    assert status['requiresHostRestart'] is True
    assert status['nextAction'] == 'start_next_segment'
    assert snapshot(root) == before
    for options in ({}, {'execution_mode': 'single_host'}):
        with pytest.raises(ValueError):
            session.advance_session(root, **options)


def test_yielded_multi_host_ledger_error_does_not_request_a_host_restart(tmp_path):
    root = synthetic_inputs(tmp_path)
    work = current(root, begin_segment=True, segment_id='segment-1', host_run_id='host-1')
    current(root, segment_id='segment-1', host_run_id='host-1',
        submission_json=submit(root, work, chapter01()))
    (root / 'frame-review-progress.json').write_text('not a JSON ledger', encoding='utf-8')
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['status'] == 'error'
    assert status['executionMode'] == 'multi_host'
    assert status['nextAction'] == 'preserve_session_use_frozen_implementation'
    assert status['requiresHostRestart'] is False
    assert snapshot(root) == before


def test_yielded_multi_host_concurrent_state_change_does_not_request_a_host_restart(tmp_path, monkeypatch):
    root = synthetic_inputs(tmp_path)
    work = current(root, begin_segment=True, segment_id='segment-1', host_run_id='host-1')
    current(root, segment_id='segment-1', host_run_id='host-1',
        submission_json=submit(root, work, chapter01()))
    state_path = root / 'analysis-session/state.json'
    before = snapshot(root)
    original = Path.read_bytes
    state_reads = [0]
    def concurrent_read(path):
        data = original(path)
        if path.resolve() == state_path.resolve():
            state_reads[0] += 1
            if state_reads[0] == 2:
                return data + b'\n'  # Simulate the final read observing another writer.
        return data
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', concurrent_read)
        status = session.read_session_status(root)
    assert state_reads[0] == 2
    assert status['status'] == 'unconfirmed'
    assert status['executionStatus'] == 'state_changed'
    assert status['nextAction'] == 'retry_analysis_status'
    assert status['executionMode'] == 'multi_host'
    assert status['requiresHostRestart'] is False
    assert snapshot(root) == before


def test_invalid_execution_mode_is_rejected_without_persisting_session(tmp_path):
    root = synthetic_inputs(tmp_path)
    before = snapshot(root, include_locks=False)
    with pytest.raises(ValueError):
        session.advance_session(root, execution_mode='invalid')
    assert snapshot(root, include_locks=False) == before


def test_new_multi_host_mode_requires_an_explicit_begin(tmp_path):
    root = synthetic_inputs(tmp_path)
    before = snapshot(root, include_locks=False)
    with pytest.raises(ValueError, match='begin_segment'):
        session.advance_session(root, execution_mode='multi_host')
    assert snapshot(root, include_locks=False) == before


def test_single_host_pending_original_request_replays_without_host_identity(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a'], [b'b']])
    only_materials(monkeypatch)
    work = current(root)
    request = material_payload(root, work)
    payload = read_object(request)
    original = session._publish
    def fail_before_part(path, value):
        if Path(path).parent.name == 'parts':
            raise OSError('injected stop after original request was journaled')
        return original(path, value)
    monkeypatch.setattr(session, '_publish', fail_before_part)
    with pytest.raises(OSError, match='journaled'):
        current(root, submission_json=request)
    monkeypatch.setattr(session, '_publish', original)
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['executionMode'] == 'single_host'
    assert status['requiresHostRestart'] is False
    assert status['executionStatus'] == 'submission_pending'
    assert status['nextAction'] == 'replay_pending_submission'
    pending_path = Path(status['pendingSubmissionPath'])
    assert read_object(pending_path) == payload
    assert file_hash(pending_path) == status['pendingSubmissionSha256']
    assert snapshot(root) == before
    recovered = current(root, submission_json=pending_path)
    assert recovered['taskId'] == 'material-002'
    assert recovered['completedTaskCount'] == 1
    assert_single_host(recovered)
    assert current(root, submission_json=pending_path) == recovered


def test_single_host_accepted_state_survives_lost_resume_and_retries_once(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a'], [b'b']])
    only_materials(monkeypatch)
    work = current(root)
    request = material_payload(root, work)
    original = session.write_json_atomically
    def fail_resume(path, value):
        if Path(path).name == 'resume.json' and value.get('completedTaskCount') == 1:
            raise OSError('injected lost response after durable acceptance')
        return original(path, value)
    monkeypatch.setattr(session, 'write_json_atomically', fail_resume)
    with pytest.raises(OSError, match='durable acceptance'):
        current(root, submission_json=request)
    monkeypatch.setattr(session, 'write_json_atomically', original)
    (root / 'analysis-session/resume.json').unlink(missing_ok=True)
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['taskId'] == 'material-002'
    assert status['completedTaskCount'] == 1
    assert_single_host(status)
    assert snapshot(root) == before
    recovered = current(root, submission_json=request)
    assert recovered['taskId'] == 'material-002'
    assert recovered['completedTaskCount'] == 1
    assert_single_host(recovered)
    assert len(read_object(root / 'analysis-session/state.json')['completed']) == 1


def test_single_host_status_is_read_only_and_freezes_input_drift(tmp_path):
    root = synthetic_inputs(tmp_path)
    current(root)
    before = snapshot(root)
    status = session.read_session_status(root)
    assert_single_host(status)
    assert status['inputBindingMatch'] is True
    assert snapshot(root) == before
    (root / 'diagnostic-analysis-prompt.md').write_text('changed frozen prompt', encoding='utf-8')
    before = snapshot(root)
    mismatch = session.read_session_status(root)
    assert mismatch['status'] == 'error'
    assert mismatch['executionMode'] == 'single_host'
    assert mismatch['inputBindingMatch'] is False
    assert mismatch['nextAction'] == 'preserve_session_use_frozen_implementation'
    assert snapshot(root) == before
    with pytest.raises(ValueError, match='changed'):
        current(root)


def test_single_host_paginated_work_keeps_mode_and_restart_policy(tmp_path, monkeypatch):
    root = synthetic_inputs(tmp_path)
    original = session._work_item
    def oversized(*args, **options):
        work = original(*args, **options)
        work['testPreservedNote'] = '局部工作项分页保持单宿主运行策略。' * 1000
        return work
    monkeypatch.setattr(session, '_work_item', oversized)
    work = session.advance_session(root)
    assert work['workItemPages']
    restored = full_work(work)
    for key in ('executionMode', 'executionStatus', 'requiresHostRestart', 'nextAction'):
        assert work[key] == restored[key]
    assert_single_host(restored)
    for page in work['workItemPages']:
        assert page['byteSize'] == Path(page['path']).stat().st_size <= 8000
        assert page['sha256'] == file_hash(page['path'])
    assert restored['testPreservedNote'] == '局部工作项分页保持单宿主运行策略。' * 1000


def test_compact_cli_defaults_to_single_host_and_preserves_read_only_status(tmp_path):
    root = synthetic_inputs(tmp_path)
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(['analysis-session', '--compact', '--output-dir', str(tmp_path)])
    assert code == 0
    receipt = json.loads(output.getvalue())
    assert_single_host(receipt)
    work = full_work(read_object(receipt['resumePath']))
    request = submit(root, work, chapter01())
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(['analysis-session', '--compact', '--output-dir', str(tmp_path),
            '--execution-mode', 'single_host', '--submission-json', str(request)])
    assert code == 0
    accepted = json.loads(output.getvalue())
    assert accepted['taskId'] == 'chapter02'
    assert_single_host(accepted)
    before = snapshot(root)
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(['analysis-status', '--compact', '--output-dir', str(tmp_path)])
    assert code == 0
    assert_single_host(json.loads(output.getvalue()))
    assert snapshot(root) == before


def test_default_single_host_accepts_30_materials_and_34_tasks_without_forced_yield(tmp_path):
    root = thirty_material_inputs(tmp_path)
    inputs = inspect_inputs(root)
    assert len(inputs['materialIds']) == 30
    assert len(inputs['frames']) == 180
    refs = [frame['frameRef'] for frame in inputs['frames']]
    chapter_parts = dict(authored_parts(inputs['manifestSha256'])[:3])
    chapter_parts['chapter04'] = synthetic_final_chapter(inputs['manifestSha256'], refs)
    accepted_tasks = []
    exact_parts = {}
    work = current(root)
    while work['status'] != 'ready_for_validation':
        assert_single_host(work)
        task_id = work['taskId']
        if task_id.startswith('material-'):
            assert len(work['frameReview']['imagesToView']) <= 6
            expected_mid = inputs['materialIds'][int(task_id.split('-')[1]) - 1]
            assert {image['materialId'] for image in work['frameReview']['imagesToView']} <= {expected_mid}
            request = material_payload(root, work)
        else:
            request = submit(root, work, chapter_parts[task_id])
        exact_parts[task_id] = read_object(request)['part']
        accepted_tasks.append(task_id)
        work = current(root, submission_json=request)
        assert work['completedTaskCount'] == len(accepted_tasks)
        assert current(root, submission_json=request) == work
        assert work['executionStatus'] != 'yield_required'
        assert work['requiresHostRestart'] is False
    expected_tasks = ['chapter01', 'chapter02', 'chapter03'] + [
        f'material-{number:03d}' for number in range(1, 31)] + ['chapter04']
    assert accepted_tasks == expected_tasks
    assert_single_host(work, complete=True)
    assert work['completedTaskCount'] == 34
    assert work['contentReviewed'] is False
    assert work['finalValidationRequired'] is True
    state = read_object(root / 'analysis-session/state.json')
    assert state['executionMode'] == 'single_host'
    assert not state.get('segmentHistory')
    assert [saved['taskId'] for saved in state['completed']] == expected_tasks
    for saved in state['completed']:
        part_path = root / 'analysis-session' / saved['partPath']
        assert read_object(part_path) == exact_parts[saved['taskId']]
        assert file_hash(part_path) == saved['partSha256']
    ledger = read_object(root / 'frame-review-progress.json')
    assert ledger['status'] == 'complete'
    assert ledger['completedFrameRefs'] == refs
    assert len(ledger['visualObservationCache']) == inputs['uniqueImageCount']
    candidate_path = Path(work['analysisJson'])
    candidate = read_object(candidate_path)
    assert [row['materialId'] for row in candidate['materialAnalyses']] == inputs['materialIds']
    assert len(read_object(candidate_path.with_suffix('.assembly.json'))['sources']) == 34
    assert verify_assembly(candidate_path, inputs)
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['status'] == 'ready_for_validation'
    assert_single_host(status, complete=True)
    assert status['nextAction'] == 'validate-analysis'
    assert snapshot(root) == before
