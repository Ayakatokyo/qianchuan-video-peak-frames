"""Offline segment ownership, durable submission recovery and bounded handoffs.

These fixtures exercise the local contract, not host compaction, automatic host
continuation, native image understanding or business acceptance.
"""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import analysis_session as session
import run_standard_analysis as cli
from analysis_handoff import file_hash, inspect_inputs, read_object, verify_assembly
from analysis_task_io import restore_task_pages
from analysis_units_lib import publish_analysis_units
from test_analysis_session import chapter01, full_work, material, only_materials, submit, write
from test_analysis_session_end_to_end import authored_parts, synthetic_inputs
from test_analysis_units import example_analysis
from test_frame_review import inputs as frame_inputs


def snapshot(directory, *, include_locks=True):
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()
            and (include_locks or not path.name.endswith('.lock'))}


def begin(root, segment_id='segment-1', host_run_id='host-1', **options):
    return full_work(session.advance_session(root, begin_segment=True,
        segment_id=segment_id, host_run_id=host_run_id, **options))


def advance(root, segment_id='segment-1', host_run_id='host-1', **options):
    return full_work(session.advance_session(root, segment_id=segment_id,
        host_run_id=host_run_id, **options))


def material_payload(root, work):
    review = work['frameReview']
    part = material(review['materialId'], review['frameRefs'])
    for frame in part['materialAnalyses'][0]['highlightAnalyses']:
        frame['interpretationStatus'] = 'observation_only'
        frame.pop('limitation')
    extra = {}
    if review['imagesToView']:
        batch = copy.deepcopy(review['checkpointTemplate'])
        for row in batch['observations']:
            row['visualObservation'] = '桌面上展示合成测试色块，没有可验证的商业信息。'
        extra['frameObservations'] = batch
    return submit(root, work, part, **extra)


def assert_yield(work, next_task):
    assert work['status'] == 'in_progress'
    assert work['executionStatus'] == 'yield_required'
    assert work['nextAction'] == 'start_next_segment'
    assert work['taskId'] == next_task
    assert not {'factsPages', 'contractPages', 'referencePages', 'frameReview'} & work.keys()


def test_legacy_session_still_continues_without_segment_identity(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[bytes([n])] for n in range(4)])
    only_materials(monkeypatch)
    work = full_work(session.advance_session(root))
    for n in range(4):
        assert work['taskId'] == f'material-{n + 1:03d}'
        work = full_work(session.advance_session(root,
            submission_json=material_payload(root, work)))
        assert work.get('executionStatus') != 'yield_required'
    assert work['status'] == 'ready_for_validation'
    assert work['completedTaskCount'] == 4


def test_chapter_is_a_segment_and_does_not_issue_next_chapter(tmp_path):
    root = synthetic_inputs(tmp_path)
    work = begin(root)
    assert work['taskId'] == 'chapter01'
    accepted = advance(root, submission_json=submit(root, work, chapter01()))
    assert_yield(accepted, 'chapter02')
    assert accepted['completedTaskCount'] == 1
    assert accepted['lastAcceptedTaskId'] == 'chapter01'
    assert not (root / 'analysis-session/work/chapter02').exists()
    before = snapshot(root, include_locks=False)
    assert advance(root) == accepted
    assert snapshot(root, include_locks=False) == before
    next_work = begin(root, 'segment-2', 'host-2', after_segment='segment-1')
    assert next_work['taskId'] == 'chapter02'
    assert next_work['executionStatus'] == 'active'
    assert next_work['factsPages']
    assert next_work['segmentId'] == 'segment-2'
    assert next_work['hostRunId'] == 'host-2'


def test_three_material_budget_spans_commands_and_exact_retries(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[bytes([n])] for n in range(4)])
    only_materials(monkeypatch)
    work = begin(root)
    for n in range(3):
        assert work['taskId'] == f'material-{n + 1:03d}'
        request = material_payload(root, work)
        work = advance(root, submission_json=request)
        assert work['completedTaskCount'] == n + 1
        assert advance(root, submission_json=request) == work
        if n < 2:
            assert work['executionStatus'] == 'active'
    assert_yield(work, 'material-004')
    assert not (root / 'analysis-session/work/material-004').exists()
    assert advance(root) == work
    next_work = begin(root, 'segment-2', 'host-2', after_segment='segment-1')
    assert next_work['completedTaskCount'] == 3
    assert next_work['taskId'] == 'material-004'
    done = advance(root, 'segment-2', 'host-2',
        submission_json=material_payload(root, next_work))
    assert done['status'] == 'ready_for_validation'
    assert done['executionStatus'] == 'complete'
    assert done['completedTaskCount'] == 4


def test_acceptance_before_yield_receipt_cannot_bypass_material_budget(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[bytes([n])] for n in range(4)])
    only_materials(monkeypatch)
    work = begin(root)
    for _ in range(2):
        work = advance(root, submission_json=material_payload(root, work))
    third_request = write(root / 'third-original.json', read_object(material_payload(root, work)))
    original_handoff = session._handoff
    def lose_handoff(directory, state):
        if len(state['completed']) == 3:
            raise OSError('injected crash after acceptance before yield receipt')
        return original_handoff(directory, state)
    monkeypatch.setattr(session, '_handoff', lose_handoff)
    with pytest.raises(OSError, match='before yield receipt'):
        advance(root, submission_json=third_request)
    monkeypatch.setattr(session, '_handoff', original_handoff)
    state = read_object(root / 'analysis-session/state.json')
    assert len(state['completed']) == 3
    assert state['segmentHistory'][-1]['status'] == 'active'
    unissued_fourth = {'inputSha256': state['inputSha256'], 'taskId': 'material-004',
        'frameReview': session.frame_review.review_status(root)}
    fourth_request = material_payload(root, unissued_fourth)
    before = snapshot(root, include_locks=False)
    with pytest.raises(ValueError, match='yield|successor'):
        advance(root, submission_json=fourth_request)
    assert snapshot(root, include_locks=False) == before
    status_before = snapshot(root)
    status = session.read_session_status(root)
    assert status['executionStatus'] == 'yield_required'
    assert status['completedTaskCount'] == 3
    assert snapshot(root) == status_before
    yielded = advance(root, submission_json=third_request)
    assert_yield(yielded, 'material-004')
    assert yielded['completedTaskCount'] == 3


def test_historical_host_run_id_cannot_be_reused_to_reset_segment_budget(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[bytes([n])] for n in range(3)])
    only_materials(monkeypatch)
    work = begin(root, material_limit=1)
    advance(root, submission_json=material_payload(root, work))
    work = begin(root, 'segment-2', 'host-2', after_segment='segment-1', material_limit=1)
    advance(root, 'segment-2', 'host-2', submission_json=material_payload(root, work))
    before = snapshot(root, include_locks=False)
    with pytest.raises(ValueError):
        begin(root, 'segment-3', 'host-1', after_segment='segment-2', material_limit=1)
    assert snapshot(root, include_locks=False) == before


@pytest.mark.parametrize('identity', [
    {}, {'segment_id': 'segment-1'}, {'host_run_id': 'host-1'},
    {'segment_id': 'wrong', 'host_run_id': 'host-1'},
    {'segment_id': 'segment-1', 'host_run_id': 'wrong'},
])
def test_segment_enabled_session_rejects_missing_or_wrong_owner(tmp_path, identity):
    root = synthetic_inputs(tmp_path)
    begin(root)
    before = snapshot(root, include_locks=False)
    with pytest.raises(ValueError):
        session.advance_session(root, **identity)
    assert snapshot(root, include_locks=False) == before


def test_active_owner_takeover_requires_acknowledged_host_end(tmp_path):
    root = synthetic_inputs(tmp_path)
    original = begin(root)
    before = snapshot(root, include_locks=False)
    for options in [
        {'after_segment': 'wrong'},
        {'after_segment': 'segment-1'},
        {'after_segment': 'segment-1', 'previous_host_ended': True, 'host_run_id': 'host-1'},
    ]:
        values = {'segment_id': 'segment-2', 'host_run_id': 'host-2', **options}
        with pytest.raises(ValueError):
            session.advance_session(root, begin_segment=True, **values)
        assert snapshot(root, include_locks=False) == before
    recovered = begin(root, 'segment-2', 'host-2', after_segment='segment-1',
        previous_host_ended=True)
    assert recovered['taskId'] == original['taskId'] == 'chapter01'
    assert recovered['completedTaskCount'] == 0
    assert recovered['executionStatus'] == 'active'
    with pytest.raises(ValueError):
        advance(root)


def test_soft_time_budget_allows_issued_task_then_yields(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a'], [b'b']])
    only_materials(monkeypatch)
    clock = [1000.0]
    monkeypatch.setattr(session.time, 'time', lambda: clock[0])
    work = begin(root)
    clock[0] += 481
    still_issued = advance(root)
    assert still_issued['taskId'] == work['taskId'] == 'material-001'
    assert still_issued['executionStatus'] == 'active'
    accepted = advance(root, submission_json=material_payload(root, still_issued))
    assert_yield(accepted, 'material-002')
    assert accepted['completedTaskCount'] == 1
    next_work = begin(root, 'segment-2', 'host-2', after_segment='segment-1')
    assert next_work['executionStatus'] == 'active'
    assert next_work['factsPages']
    done = advance(root, 'segment-2', 'host-2', submission_json=material_payload(root, next_work))
    assert done['status'] == 'ready_for_validation'


def test_material_count_budget_resets_per_segment(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[bytes([n])] for n in range(4)])
    only_materials(monkeypatch)
    first = begin(root, material_limit=1)
    yielded = advance(root, submission_json=material_payload(root, first))
    assert_yield(yielded, 'material-002')
    work = begin(root, 'segment-2', 'host-2', after_segment='segment-1', material_limit=2)
    work = advance(root, 'segment-2', 'host-2', submission_json=material_payload(root, work))
    assert work['executionStatus'] == 'active'
    assert work['completedTaskCount'] == 2
    work = advance(root, 'segment-2', 'host-2', submission_json=material_payload(root, work))
    assert_yield(work, 'material-004')
    assert work['completedTaskCount'] == 3
    last = begin(root, 'segment-3', 'host-3', after_segment='segment-2', material_limit=1)
    done = advance(root, 'segment-3', 'host-3', submission_json=material_payload(root, last))
    assert done['status'] == 'ready_for_validation'
    assert done['completedTaskCount'] == 4


@pytest.mark.parametrize('options', [
    {'material_limit': 0}, {'material_limit': -1}, {'material_limit': True},
    {'soft_time_budget_seconds': 0}, {'soft_time_budget_seconds': -1},
    {'soft_time_budget_seconds': float('nan')}, {'soft_time_budget_seconds': float('inf')},
])
def test_invalid_segment_budgets_fail_before_session_publication(tmp_path, options):
    root = synthetic_inputs(tmp_path)
    before = snapshot(root, include_locks=False)
    with pytest.raises(ValueError):
        begin(root, **options)
    assert snapshot(root, include_locks=False) == before


def test_read_only_status_before_start_and_after_acceptance_changes_no_files(tmp_path):
    root = synthetic_inputs(tmp_path)
    before = snapshot(root)
    not_started = session.read_session_status(root)
    assert not_started['status'] == 'not_started'
    assert snapshot(root) == before
    work = begin(root)
    advance(root, submission_json=submit(root, work, chapter01()))
    before = snapshot(root)
    state = session.read_session_status(root)
    assert state['taskId'] == 'chapter02'
    assert state['completedTaskCount'] == 1
    assert state['inputBindingMatch'] is True
    assert state['executionStatus'] == 'yield_required'
    assert state['nextAction'] == 'start_next_segment'
    assert snapshot(root) == before


@pytest.mark.parametrize('source', ['prompt', 'image', 'implementation'])
def test_read_only_status_reports_input_or_implementation_drift(tmp_path, monkeypatch, source):
    root = synthetic_inputs(tmp_path)
    begin(root)
    if source == 'prompt':
        path = root / 'diagnostic-analysis-prompt.md'
        path.write_text('different frozen prompt', encoding='utf-8')
    elif source == 'image':
        (tmp_path / 'frame-0.jpg').write_bytes(b'different JPEG input')
    else:
        original = session.file_hash
        monkeypatch.setattr(session, 'file_hash', lambda path:
            '0' * 64 if Path(path).resolve() == Path(session.__file__).resolve() else original(path))
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['status'] == 'error'
    assert status['executionStatus'] == 'input_mismatch'
    assert status['inputBindingMatch'] is False
    assert status['nextAction'] == 'preserve_session_use_frozen_implementation'
    assert status['error']
    assert snapshot(root) == before


def test_accepted_state_recovers_when_resume_receipt_is_lost(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a'], [b'b']])
    only_materials(monkeypatch)
    work = begin(root)
    request = material_payload(root, work)
    original_write = session.write_json_atomically
    def lose_response(path, value):
        if Path(path).name == 'resume.json' and value.get('completedTaskCount') == 1:
            raise OSError('injected lost resume after durable accepted state')
        return original_write(path, value)
    monkeypatch.setattr(session, 'write_json_atomically', lose_response)
    with pytest.raises(OSError, match='lost resume'):
        advance(root, submission_json=request)
    monkeypatch.setattr(session, 'write_json_atomically', original_write)
    (root / 'analysis-session/resume.json').unlink(missing_ok=True)
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['taskId'] == 'material-002'
    assert status['completedTaskCount'] == 1
    assert snapshot(root) == before
    recovered = advance(root, submission_json=request)
    assert recovered['taskId'] == 'material-002'
    assert recovered['completedTaskCount'] == 1
    assert len(read_object(root / 'analysis-session/state.json')['completed']) == 1


@pytest.mark.parametrize('interruption_point', ['before_candidate', 'before_receipt'])
def test_complete_parts_without_verified_assembly_are_not_reported_ready(
        tmp_path, monkeypatch, interruption_point):
    import analysis_handoff
    root = frame_inputs(tmp_path, [[b'a']])
    only_materials(monkeypatch)
    work = begin(root)
    request = material_payload(root, work)
    original_assemble = session.assemble_analysis
    original_write = analysis_handoff.write_json_atomically
    if interruption_point == 'before_candidate':
        def interrupted_assembly(*args, **options):
            raise OSError('injected stop before candidate assembly')
        monkeypatch.setattr(session, 'assemble_analysis', interrupted_assembly)
    else:
        def interrupted_receipt(path, value):
            if str(path).endswith('.assembly.json'):
                raise OSError('injected stop before assembly receipt')
            return original_write(path, value)
        monkeypatch.setattr(analysis_handoff, 'write_json_atomically', interrupted_receipt)
    with pytest.raises(OSError, match='injected stop'):
        advance(root, submission_json=request)
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['status'] == 'in_progress'
    assert status['completedTaskCount'] == status['taskCount'] == 1
    assert status['taskId'] is None
    assert status['inputBindingMatch'] is True
    assert status['nextAction'] == 'resume_assembly'
    assert snapshot(root) == before
    monkeypatch.setattr(session, 'assemble_analysis', original_assemble)
    monkeypatch.setattr(analysis_handoff, 'write_json_atomically', original_write)
    recovered = advance(root, submission_json=request)
    assert recovered['status'] == 'ready_for_validation'
    assert recovered['completedTaskCount'] == 1
    status = session.read_session_status(root)
    assert status['status'] == 'ready_for_validation'
    assert status['nextAction'] == 'validate-analysis'


def test_validated_intent_survives_before_part_publish_and_replays_exact_payload(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a'], [b'b']])
    only_materials(monkeypatch)
    work = begin(root)
    request = material_payload(root, work)
    payload = read_object(request)
    original_publish = session._publish
    def interrupt_before_part(path, value):
        if Path(path).parent.name == 'parts':
            raise OSError('injected interruption after immutable intent')
        return original_publish(path, value)
    monkeypatch.setattr(session, '_publish', interrupt_before_part)
    with pytest.raises(OSError, match='immutable intent'):
        advance(root, submission_json=request)
    monkeypatch.setattr(session, '_publish', original_publish)
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['taskId'] == 'material-001'
    assert status['completedTaskCount'] == 0
    assert status['nextAction'] == 'replay_pending_submission'
    pending = Path(status['pendingSubmissionPath'])
    assert pending.parent == root / 'analysis-session/submissions'
    assert pending.name.startswith('material-001.')
    assert read_object(pending) == payload
    assert file_hash(pending) == status['pendingSubmissionSha256']
    assert snapshot(root) == before
    recovered = advance(root, submission_json=pending)
    assert recovered['taskId'] == 'material-002'
    assert recovered['completedTaskCount'] == 1
    assert read_object(pending) == payload
    assert advance(root, submission_json=pending) == recovered


def test_invalid_submission_is_not_published_as_replayable_intent(tmp_path):
    root = synthetic_inputs(tmp_path)
    work = begin(root)
    bad = chapter01()
    bad['overallConclusion'] = None
    with pytest.raises(ValueError):
        advance(root, submission_json=submit(root, work, bad))
    assert not list((root / 'analysis-session/submissions').glob('*.json'))
    before = snapshot(root)
    status = session.read_session_status(root)
    assert not status.get('pendingSubmissionPath')
    assert status['completedTaskCount'] == 0
    assert snapshot(root) == before


def test_paginated_work_item_keeps_execution_and_owner_metadata(tmp_path, monkeypatch):
    root = synthetic_inputs(tmp_path)
    original = session._work_item
    def oversized(*args, **options):
        value = original(*args, **options)
        value['testLargeSourceNote'] = '原始测试信息必须完整保留。' * 1000
        return value
    monkeypatch.setattr(session, '_work_item', oversized)
    result = session.advance_session(root, begin_segment=True,
        segment_id='segment-1', host_run_id='host-1')
    assert result['workItemPages']
    restored = full_work(result)
    for field in ('executionStatus', 'nextAction', 'segmentId', 'hostRunId'):
        assert result[field] == restored[field]
    for page in result['workItemPages']:
        assert Path(page['path']).stat().st_size == page['byteSize'] <= 8000
        assert file_hash(page['path']) == page['sha256']
    assert restored['testLargeSourceNote'] == '原始测试信息必须完整保留。' * 1000


def test_compact_cli_transmits_yield_and_read_only_status(tmp_path):
    root = synthetic_inputs(tmp_path)
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(['analysis-session', '--compact', '--output-dir', str(tmp_path),
            '--begin-segment', '--segment-id', 'segment-1', '--host-run-id', 'host-1'])
    assert code == 0
    receipt = json.loads(output.getvalue())
    assert receipt['executionStatus'] == 'active'
    assert receipt['nextAction'] == 'continue_current_task'
    assert receipt['segmentId'] == 'segment-1'
    assert receipt['hostRunId'] == 'host-1'
    work = full_work(read_object(receipt['resumePath']))
    request = submit(root, work, chapter01())
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(['analysis-session', '--compact', '--output-dir', str(tmp_path),
            '--segment-id', 'segment-1', '--host-run-id', 'host-1',
            '--submission-json', str(request)])
    assert code == 0
    yielded = json.loads(output.getvalue())
    assert yielded['executionStatus'] == 'yield_required'
    assert yielded['nextAction'] == 'start_next_segment'
    assert yielded['lastAcceptedTaskId'] == 'chapter01'
    assert_yield(read_object(yielded['resumePath']), 'chapter02')
    before = snapshot(root)
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(['analysis-status', '--output-dir', str(tmp_path)])
    assert code == 0
    state = json.loads(output.getvalue())
    assert state['taskId'] == 'chapter02'
    assert state['executionStatus'] == 'yield_required'
    assert snapshot(root) == before


def thirty_material_inputs(run):
    """Publish 30 materials and 180 actual tiny JPEGs without external calls."""
    facts = example_analysis()
    original_material = copy.deepcopy(facts['materials'][0])
    original_script = copy.deepcopy(facts['scripts']['materials'][0])
    audience = facts['audience']['metrics']['SHOW_COUNT']
    for metric_name in ('CLICK_COUNT', 'TRANSACTION_AMOUNT'):
        facts['audience']['metrics'][metric_name] = copy.deepcopy(audience)
    facts['materials'] = []
    facts['scripts']['materials'] = []
    highlights = []
    for n in range(1, 31):
        mid = f'm-{n}'
        facts['materials'].append({**copy.deepcopy(original_material),
            'rank': n, 'materialId': mid, 'materialName': f'合成素材{n}'})
        facts['scripts']['materials'].append({**copy.deepcopy(original_script), 'materialId': mid})
        frames = []
        for frame_number in range(6):
            path = run / f'{mid}-{frame_number}.jpg'
            Image.new('RGB', (16, 16), (n * 7 % 255, frame_number * 37, n)).save(path, format='JPEG')
            frames.append({'materialId': mid, 'rankType': f'peak_{frame_number}',
                'durationSec': frame_number, 'metricValue': 1, 'framePath': str(path),
                'evidenceRef': f'highlight:{mid}:peak_{frame_number}'})
        highlights.append({'materialId': mid, 'frames': frames, 'missingFrames': [],
            'frameCoverage': {'succeededFrameCount': 6}})
    facts['highlights']['materials'] = highlights
    root = run / 'diagnostic-analysis'
    publish_analysis_units(root, facts)
    (root / 'diagnostic-analysis-prompt.md').write_text(
        'Offline 30-material synthetic authoring contract.', encoding='utf-8')
    return root


def synthetic_final_chapter(manifest_sha, refs):
    template = authored_parts(manifest_sha)[-1][1]
    template['highlightDashboard']['groups'][0]['frameRefs'] = list(refs)
    for field in ('commonConclusion', 'derivativeDirections'):
        template['highlightDashboard']['groups'][0][field][0]['evidenceRefs'] = list(refs)
    for index, group in enumerate(template['customClassification']['groups']):
        selected = refs[index::3]
        group['frameRefs'] = selected
        for field in ('commonConclusion', 'derivativeDirections'):
            group[field][0]['evidenceRefs'] = selected
    return template


def test_thirty_materials_complete_34_tasks_across_explicit_host_segments(tmp_path):
    root = thirty_material_inputs(tmp_path)
    inputs = inspect_inputs(root)
    assert len(inputs['materialIds']) == 30
    assert len(inputs['frames']) == 180
    parts_by_task = dict(authored_parts(inputs['manifestSha256'])[:3])
    frame_refs = [frame['frameRef'] for frame in inputs['frames']]
    parts_by_task['chapter04'] = synthetic_final_chapter(inputs['manifestSha256'], frame_refs)
    segment_number = 1
    owner = ('segment-1', 'host-1')
    work = begin(root, *owner)
    accepted_tasks = []
    stored_parts = {}
    yielded_material_counts = []
    materials_in_segment = 0
    while work['status'] != 'ready_for_validation':
        task_id = work['taskId']
        if task_id.startswith('material-'):
            request = material_payload(root, work)
            materials_in_segment += 1
        else:
            request = submit(root, work, parts_by_task[task_id])
        stored_parts[task_id] = read_object(request)['part']
        accepted_tasks.append(task_id)
        work = advance(root, *owner, submission_json=request)
        assert work['completedTaskCount'] == len(accepted_tasks)
        assert advance(root, *owner, submission_json=request) == work
        if work['executionStatus'] == 'yield_required':
            assert_yield(work, work['taskId'])
            if materials_in_segment:
                yielded_material_counts.append(materials_in_segment)
            materials_in_segment = 0
            previous = owner[0]
            segment_number += 1
            owner = (f'segment-{segment_number}', f'host-{segment_number}')
            before = snapshot(root)
            status = session.read_session_status(root)
            assert status['completedTaskCount'] == len(accepted_tasks)
            assert status['nextAction'] == 'start_next_segment'
            assert snapshot(root) == before
            work = begin(root, *owner, after_segment=previous)
    expected_tasks = ['chapter01', 'chapter02', 'chapter03'] + [
        f'material-{number:03d}' for number in range(1, 31)] + ['chapter04']
    assert accepted_tasks == expected_tasks
    assert work['completedTaskCount'] == 34
    assert work['executionStatus'] == 'complete'
    assert yielded_material_counts == [3] * 10
    assert segment_number == 14  # Three chapter segments, ten material segments, final synthesis.
    state = read_object(root / 'analysis-session/state.json')
    assert [row['taskId'] for row in state['completed']] == expected_tasks
    for saved in state['completed']:
        path = root / 'analysis-session' / saved['partPath']
        assert read_object(path) == stored_parts[saved['taskId']]
        assert file_hash(path) == saved['partSha256']
    ledger = read_object(root / 'frame-review-progress.json')
    assert ledger['status'] == 'complete'
    assert ledger['completedFrameRefs'] == frame_refs
    assert len(ledger['visualObservationCache']) == inputs['uniqueImageCount']
    candidate_path = Path(work['analysisJson'])
    candidate = read_object(candidate_path)
    assert len(candidate['materialAnalyses']) == 30
    assert [item['materialId'] for item in candidate['materialAnalyses']] == inputs['materialIds']
    assert verify_assembly(candidate_path, inputs)
    receipt = read_object(candidate_path.with_suffix('.assembly.json'))
    assert len(receipt['sources']) == 34
    before = snapshot(root)
    status = session.read_session_status(root)
    assert status['status'] == 'ready_for_validation'
    assert status['executionStatus'] == 'complete'
    assert status['nextAction'] == 'validate-analysis'
    assert snapshot(root) == before
