"""Bounded authoring work, durable observations and exact retry recovery."""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import analysis_session as session
import analysis_tasks
import frame_review
import run_standard_analysis as cli
from analysis_handoff import file_hash, read_object
from test_analysis_reader import AnalysisReaderTests
from test_frame_review import inputs as frame_inputs


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False))
    return path


def chapter01():
    return {'analysisProvenance': {'author': 'current_model', 'businessTextGeneration': 'direct'},
            'overallConclusion': '模型直接撰写的原文。',
            'portfolioDimensionConclusions': {'commercial': {'conclusion': '保留原文', 'evidenceRefs': []}}}


def submit(root, work, part, **extra):
    return write(root / 'submission.json', {'inputSha256': work['inputSha256'],
        'taskId': work['taskId'], 'part': part, **extra})


def only_materials(monkeypatch):
    original = analysis_tasks._packets
    def packets(*args, **kwargs):
        inputs, binding, rows = original(*args, **kwargs)
        return inputs, binding, [row for row in rows if row[0]['taskId'].startswith('material-')]
    monkeypatch.setattr(session, '_packets', packets)


def material(mid, refs):
    return {'materialAnalyses': [{'materialId': mid, 'audienceConclusion': '现有人群数据不足。',
        'scriptConclusion': '以原脚本为准。', 'interactionConclusion': '互动不足以归因。',
        'integratedConclusion': '当前事实限定本条结论。', 'decision': 'hold',
        'nextAction': '补充对照样本。', 'frameCoverageNote': '', 'evidenceRefs': refs,
        'highlightAnalyses': [{'frameRef': r, 'visualObservation': '桌面上的产品瓶。',
            'interpretationStatus': 'insufficient_evidence', 'limitation': '静态画面不能验证转化作用。'} for r in refs]}]}


def test_current_work_is_bounded_and_does_not_repeat_all_history(tmp_path):
    root = AnalysisReaderTests().inputs(tmp_path)
    result = session.advance_session(root)
    assert result['taskId'] == 'chapter01'
    assert result['contentReviewed'] is False
    assert len(json.dumps(result, ensure_ascii=False).encode()) < 8000
    assert all(Path(p['path']).is_file() for p in result['factsPages'])
    before = read_object(root / 'analysis-session/state.json')
    assert session.advance_session(root) == result
    assert read_object(root / 'analysis-session/state.json') == before


def test_submit_preserves_text_advances_once_and_retry_is_idempotent(tmp_path):
    root = AnalysisReaderTests().inputs(tmp_path)
    initial = session.advance_session(root)
    request = submit(root, initial, chapter01())
    next_work = session.advance_session(root, submission_json=request)
    assert next_work['taskId'] == 'chapter02'
    assert session.advance_session(root, submission_json=request) == next_work
    state = read_object(root / 'analysis-session/state.json')
    assert len(state['completed']) == 1
    saved = root / 'analysis-session' / state['completed'][0]['partPath']
    assert read_object(saved) == chapter01()
    assert state['completed'][0]['elapsedSeconds'] >= 0
    assert 'model' not in state['completed'][0]['measurement'].lower()
    conflicting = copy.deepcopy(chapter01())
    conflicting['overallConclusion'] = '另一份正文'
    with pytest.raises(ValueError, match='already submitted'):
        session.advance_session(root, submission_json=submit(root, initial, conflicting))


def test_invalid_stale_or_wrong_task_never_advances(tmp_path):
    root = AnalysisReaderTests().inputs(tmp_path)
    initial = session.advance_session(root)
    state = root / 'analysis-session/state.json'
    before = state.read_bytes()
    for changes in ({'taskId': 'chapter04'}, {'inputSha256': 'stale'}, {'part': {'overallConclusion': None}}):
        payload = {'taskId': initial['taskId'], 'inputSha256': initial['inputSha256'], 'part': chapter01()}
        payload.update(changes)
        with pytest.raises(ValueError):
            session.advance_session(root, submission_json=write(root / 'bad.json', payload))
        assert state.read_bytes() == before
    (root / 'diagnostic-analysis-prompt.md').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        session.advance_session(root)


def test_partial_images_survive_and_material_requires_full_review(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a', b'b', b'c', b'd']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    review = work['frameReview']
    payload = copy.deepcopy(review['checkpointTemplate'])
    payload['observations'] = [{'sourceFrameRef': review['imagesToView'][0]['frameRef'],
                              'visualObservation': '桌面上的产品瓶。'}]
    result = session.advance_session(root, observations_json=write(root / 'seen.json', payload))
    assert len(result['frameReview']['imagesToView']) == 3
    assert result['completedTaskCount'] == 0
    before = file_hash(root / 'frame-review-progress.json')
    with pytest.raises(ValueError, match='unreviewed'):
        session.advance_session(root, submission_json=submit(root, work, material('m-1', review['frameRefs'])))
    assert file_hash(root / 'frame-review-progress.json') == before
    assert session.advance_session(root)['frameReview'] == result['frameReview']


def test_part_and_checkpoint_commit_share_snapshot_and_advance_cached_material(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a', b'b'], [b'a', b'b']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    batch = work['frameReview']['checkpointTemplate']
    for observation in batch['observations']:
        observation['visualObservation'] = '桌面上的产品瓶。'
    request = submit(root, work, material('m-1', work['frameReview']['frameRefs']), frameObservations=batch)
    original = analysis_tasks._snapshot
    calls = []
    def snapshot(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(analysis_tasks, '_snapshot', snapshot)
    next_work = session.advance_session(root, submission_json=request)
    assert len(calls) == 1
    assert next_work['taskId'] == 'material-002'
    assert not next_work['frameReview']['imagesToView']
    assert len(next_work['frameReview']['cachedObservations']) == 2
    assert next_work['contextHandoff']['canReleaseCompletedWork']
    state = read_object(root / 'analysis-session/state.json')
    stored = root / 'analysis-session' / state['completed'][0]['partPath']
    stored.write_text('{}')
    with pytest.raises(ValueError, match='part.*changed'):
        session.advance_session(root)


def test_invalid_part_cannot_commit_valid_observations(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    batch = work['frameReview']['checkpointTemplate']
    batch['observations'][0]['visualObservation'] = '真实观察'
    before = (root / 'frame-review-progress.json').read_bytes()
    broken = material('m-1', work['frameReview']['frameRefs'])
    broken['materialAnalyses'][0]['decision'] = 'INVALID'
    with pytest.raises(ValueError):
        session.advance_session(root, submission_json=submit(root, work, broken, frameObservations=batch))
    assert (root / 'frame-review-progress.json').read_bytes() == before


def test_cli_session_does_not_bootstrap_dependencies(tmp_path, monkeypatch):
    AnalysisReaderTests().inputs(tmp_path)
    monkeypatch.setattr(cli, 'ensure_prepare_dependencies', lambda **kw: pytest.fail('dependency bootstrap'))
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(['analysis-session', '--output-dir', str(tmp_path)])
    assert code == 0
    assert json.loads(output.getvalue())['taskId'] == 'chapter01'


def test_category_error_exposes_allowed_values():
    from final_report_lib import _validate_highlight_dashboard_group, FinalReportError
    with pytest.raises(FinalReportError, match='portrait, product, other'):
        _validate_highlight_dashboard_group({'category': 'made-up'}, 0)


def test_long_image_paths_have_bounded_recoverable_work_item(tmp_path, monkeypatch):
    nested = tmp_path / ('中文客户目录' * 12) / ('中文运行目录' * 12)
    nested.mkdir(parents=True)
    root = frame_inputs(nested, [[bytes([i]) for i in range(6)]])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    from analysis_task_io import pretty_json, restore_task_pages
    assert len((pretty_json(work) + '\n').encode()) <= 8000
    assert work['workItemPages']
    pages = [read_object(p['path'])['records'] for p in work['workItemPages']]
    restored = restore_task_pages(pages)
    assert len(restored['frameReview']['imagesToView']) == 6
    assert all(Path(f['framePath']).is_file() for f in restored['frameReview']['imagesToView'])


def test_completed_image_stash_can_retry_without_rewatching(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    batch = work['frameReview']['checkpointTemplate']
    batch['observations'][0]['visualObservation'] = '桌面上的产品瓶。'
    path = write(root / 'seen.json', batch)
    complete = session.advance_session(root, observations_json=path)
    assert complete['frameReview']['status'] == 'material_reviewed'
    assert session.advance_session(root, observations_json=path) == complete


def test_same_named_session_directories_cannot_reuse_another_candidate(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[]])
    only_materials(monkeypatch)
    candidates = []
    for parent, text in [('first', '第一份模型结论'), ('second', '第二份模型结论')]:
        directory = root / parent / 'session'
        work = session.advance_session(root, session_dir=directory)
        part = material('m-1', [])
        part['materialAnalyses'][0]['integratedConclusion'] = text
        done = session.advance_session(root, session_dir=directory,
            submission_json=submit(root, work, part))
        candidate = Path(done['analysisJson'])
        assert read_object(candidate)['materialAnalyses'][0]['integratedConclusion'] == text
        candidates.append(candidate)
    assert candidates[0] != candidates[1]


def test_retry_recovers_exit_between_frame_and_session_commit(tmp_path, monkeypatch):
    root = frame_inputs(tmp_path, [[b'a']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    batch = work['frameReview']['checkpointTemplate']
    batch['observations'][0]['visualObservation'] = '桌面上的产品瓶。'
    request = submit(root, work, material('m-1', work['frameReview']['frameRefs']), frameObservations=batch)
    original = session.write_json_atomically
    def interrupt(path, value):
        if Path(path).name == 'state.json' and value['completed']:
            raise OSError('simulated process exit after frame checkpoint')
        return original(path, value)
    monkeypatch.setattr(session, 'write_json_atomically', interrupt)
    with pytest.raises(OSError):
        session.advance_session(root, submission_json=request)
    assert frame_review.review_status(root)['status'] == 'complete'
    monkeypatch.setattr(session, 'write_json_atomically', original)
    done = session.advance_session(root, submission_json=request)
    assert done['status'] == 'ready_for_validation'
    assert len(read_object(root / 'analysis-session/state.json')['completed']) == 1


def test_retry_recovers_exit_between_candidate_and_assembly_receipt(tmp_path, monkeypatch):
    import analysis_handoff
    root = frame_inputs(tmp_path, [[]])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    original = analysis_handoff.write_json_atomically
    def interrupt(path, value):
        if str(path).endswith('.assembly.json'):
            raise OSError('simulated process exit before assembly receipt')
        return original(path, value)
    monkeypatch.setattr(analysis_handoff, 'write_json_atomically', interrupt)
    with pytest.raises(OSError):
        session.advance_session(root, submission_json=submit(root, work, material('m-1', [])))
    monkeypatch.setattr(analysis_handoff, 'write_json_atomically', original)
    assert session.advance_session(root)['status'] == 'ready_for_validation'
