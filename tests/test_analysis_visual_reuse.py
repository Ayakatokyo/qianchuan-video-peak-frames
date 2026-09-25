"""A session copies observed words and model labels without inventing analysis."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import analysis_session as session
from analysis_handoff import read_object
from analysis_task_io import restore_task_pages
from test_analysis_session import material, only_materials, submit, write
from test_frame_review import inputs
from test_analysis_session_end_to_end import synthetic_inputs, authored_parts, OBSERVATIONS


def observe(work, text='  模型看到瓶身与标签。\n原文保留。  '):
    payload = copy.deepcopy(work['frameReview']['checkpointTemplate'])
    for entry in payload['observations']:
        entry['visualObservation'] = text
    return payload


def omit_observations(part):
    result = copy.deepcopy(part)
    for item in result['materialAnalyses']:
        for frame in item['highlightAnalyses']:
            frame.pop('visualObservation')
    return result


def test_session_material_can_omit_observations_and_copies_exact_cache_across_materials(tmp_path, monkeypatch):
    root = inputs(tmp_path, [[b'a', b'a'], [b'a']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    batch = observe(work)
    first = omit_observations(material('m-1', work['frameReview']['frameRefs']))
    request = submit(root, work, first, frameObservations=batch)
    second = session.advance_session(root, submission_json=request)
    assert session.advance_session(root, submission_json=request) == second
    assert second['frameReview']['imagesToView'] == []
    second_part = omit_observations(material('m-2', second['frameReview']['frameRefs']))
    second_part['materialAnalyses'][0]['integratedConclusion'] = '第二素材有自己的经营结论。'
    done = session.advance_session(root, submission_json=submit(root, second, second_part))
    candidate = read_object(done['analysisJson'])
    for item in candidate['materialAnalyses']:
        assert all(frame['visualObservation'] == '  模型看到瓶身与标签。\n原文保留。  '
                   for frame in item['highlightAnalyses'])
    assert candidate['materialAnalyses'][1]['integratedConclusion'] == '第二素材有自己的经营结论。'
    receipt = read_object(root / 'analysis-session/state.json')['completed'][0]
    assert len(receipt['visualAssembly']['observationCopies']) == 2
    assert receipt['visualAssembly']['observationCopies'][0]['sourceFrameRef'] == 'highlight:m-1:peak_0'


def test_missing_observation_cannot_be_filled_from_material_text_or_another_image(tmp_path, monkeypatch):
    root = inputs(tmp_path, [[b'a', b'b']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    batch = observe(work)
    batch['observations'] = batch['observations'][:1]
    session.advance_session(root, observations_json=write(root / 'partial.json', batch))
    before = (root / 'frame-review-progress.json').read_bytes()
    with pytest.raises(ValueError, match='unreviewed'):
        session.advance_session(root, submission_json=submit(
            root, work, omit_observations(material('m-1', work['frameReview']['frameRefs']))))
    assert (root / 'frame-review-progress.json').read_bytes() == before
    assert read_object(root / 'analysis-session/state.json')['completed'] == []


def test_submission_template_requests_only_business_text_for_material(tmp_path, monkeypatch):
    root = inputs(tmp_path, [[b'a']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    template = read_object(work['submissionTemplatePath'])
    frame = template['part']['materialAnalyses'][0]['highlightAnalyses'][0]
    assert 'visualObservation' not in frame
    assert 'commercialInterpretation' in frame
    contract = restore_task_pages([read_object(page['path'])['records'] for page in work['contractPages']])
    assert contract['outputSkeleton'] == template['part']


def test_bad_material_does_not_save_valid_pending_observations(tmp_path, monkeypatch):
    root = inputs(tmp_path, [[b'a']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    part = omit_observations(material('m-1', work['frameReview']['frameRefs']))
    part['materialAnalyses'][0]['decision'] = 'invalid'
    before = (root / 'frame-review-progress.json').read_bytes()
    with pytest.raises(ValueError):
        session.advance_session(root, submission_json=submit(root, work, part, frameObservations=observe(work)))
    assert (root / 'frame-review-progress.json').read_bytes() == before


def test_observation_copy_retry_survives_exit_after_ledger_commit(tmp_path, monkeypatch):
    root = inputs(tmp_path, [[b'a']])
    only_materials(monkeypatch)
    work = session.advance_session(root)
    request = submit(root, work, omit_observations(material('m-1', work['frameReview']['frameRefs'])),
                     frameObservations=observe(work))
    original = session.write_json_atomically
    def interrupt(path, value):
        if Path(path).name == 'state.json' and value['completed']:
            raise OSError('exit after ledger commit')
        return original(path, value)
    monkeypatch.setattr(session, 'write_json_atomically', interrupt)
    with pytest.raises(OSError):
        session.advance_session(root, submission_json=request)
    monkeypatch.setattr(session, 'write_json_atomically', original)
    result = session.advance_session(root, submission_json=request)
    assert result['status'] == 'ready_for_validation'
    assert len(read_object(root / 'analysis-session/state.json')['completed']) == 1


def to_chapter04(run, *, category='other'):
    root = synthetic_inputs(run)
    from analysis_handoff import inspect_inputs
    authored = authored_parts(inspect_inputs(root)['manifestSha256'])
    work = session.advance_session(root)
    for _, part in authored[:3]:
        work = session.advance_session(root, submission_json=submit(root, work, part))
    batch = copy.deepcopy(work['frameReview']['checkpointTemplate'])
    for entry in batch['observations']:
        entry['visualObservation'] = OBSERVATIONS[entry['sourceFrameRef']]
        label = category.get(entry['sourceFrameRef']) if isinstance(category, dict) else category
        if label is not None:
            entry['standardCategory'] = label
    work = session.advance_session(root, submission_json=submit(
        root, work, omit_observations(authored[3][1]), frameObservations=batch))
    return root, work, authored


def test_chapter04_template_and_submission_reuse_model_labels_with_full_coverage(tmp_path):
    root, work, authored = to_chapter04(tmp_path)
    template = read_object(work['submissionTemplatePath'])['part']
    groups = template['highlightDashboard']['groups']
    assert [group['category'] for group in groups] == ['other']
    assert 'frameRefs' not in groups[0]
    part = copy.deepcopy(authored[4][1])
    part['highlightDashboard']['groups'][0].pop('frameRefs')
    done = session.advance_session(root, submission_json=submit(root, work, part))
    candidate = read_object(done['analysisJson'])
    assert candidate['highlightDashboard'] == authored[4][1]['highlightDashboard']
    assert candidate['customClassification'] == authored[4][1]['customClassification']
    receipt = read_object(root / 'analysis-session/state.json')['completed'][-1]
    assert receipt['visualAssembly']['standardGroupCopies'] == [
        {'category': 'other', 'frameRefs': list(OBSERVATIONS)}]


def test_legacy_observations_require_explicit_classification_and_still_finish(tmp_path):
    root, work, authored = to_chapter04(tmp_path, category=None)
    template = read_object(work['submissionTemplatePath'])['part']
    assert 'frameRefs' in template['highlightDashboard']['groups'][0]
    incomplete = copy.deepcopy(authored[4][1])
    incomplete['highlightDashboard']['groups'][0].pop('frameRefs')
    with pytest.raises(ValueError, match='legacy observations'):
        session.advance_session(root, submission_json=submit(root, work, incomplete))
    done = session.advance_session(root, submission_json=submit(root, work, authored[4][1]))
    assert read_object(done['analysisJson'])['highlightDashboard'] == authored[4][1]['highlightDashboard']


def test_explicit_standard_classification_cannot_override_saved_model_labels(tmp_path):
    root, work, authored = to_chapter04(tmp_path)
    changed = copy.deepcopy(authored[4][1])
    changed['highlightDashboard']['groups'][0]['category'] = 'portrait'
    with pytest.raises(ValueError, match='conflict'):
        session.advance_session(root, submission_json=submit(root, work, changed))
    assert len(read_object(root / 'analysis-session/state.json')['completed']) == 4


def test_mixed_legacy_ledger_cannot_override_a_label_that_was_recorded(tmp_path):
    root, work, authored = to_chapter04(tmp_path, category={'highlight:m-1:peak_0': 'portrait'})
    with pytest.raises(ValueError, match='conflict'):
        session.advance_session(root, submission_json=submit(root, work, authored[4][1]))
    assert len(read_object(root / 'analysis-session/state.json')['completed']) == 4


def test_derived_groups_do_not_bypass_the_model_prose_or_membership_validator(tmp_path):
    root, work, authored = to_chapter04(tmp_path)
    broken = copy.deepcopy(authored[4][1])
    broken['highlightDashboard']['groups'][0].pop('frameRefs')
    broken['highlightDashboard']['groups'][0]['commonConclusion'][0]['evidenceRefs'] = ['unknown:image']
    with pytest.raises(ValueError):
        session.advance_session(root, submission_json=submit(root, work, broken))
    assert len(read_object(root / 'analysis-session/state.json')['completed']) == 4
