"""Exercise the default CLI without jq or a legacy work-item decoder."""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_standard_analysis as cli
import analysis_navigation
import analysis_session
from analysis_handoff import inspect_inputs, read_object, verify_assembly
from test_analysis_session_end_to_end import synthetic_inputs, authored_parts, OBSERVATIONS, pages


def invoke(run, flag=None, payload=None, *, legacy=False):
    args = ['analysis-session', '--output-dir', str(run)]
    if legacy:
        args.append('--legacy-work-item')
    if flag:
        request = run / 'diagnostic-analysis/current-request.json'
        request.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        args += [flag, str(request)]
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(args)
    assert code == 0, output.getvalue()
    assert len(output.getvalue().encode()) <= 8000
    return json.loads(output.getvalue())


def absolute(work, path):
    return Path(work['navigation']['basePath']) / path


def indexed_pages(work, section):
    result = []
    pointer = work['navigation'][section]
    while pointer:
        page = read_object(absolute(work, pointer))
        assert isinstance(page['entries'], list)
        result += [{**entry, 'path': str(absolute(work, entry['path']))} for entry in page['entries']]
        pointer = page['nextPage']
    return result


def request(work, part, **extra):
    return {'inputSha256': work['inputSha256'], 'taskId': work['taskId'], 'part': part, **extra}


def test_default_cli_completes_all_slots_using_only_stable_navigation(tmp_path):
    root = synthetic_inputs(tmp_path)
    source = inspect_inputs(root)
    authored = authored_parts(source['manifestSha256'])
    work = invoke(tmp_path)
    assert work['format'] == 'analysis-navigation-v1'
    for task_id, part in authored[:3]:
        assert work['taskId'] == task_id
        assert pages(indexed_pages(work, 'facts'))['taskId'] == task_id
        work = invoke(tmp_path, '--submission-json', request(work, part))
    assert work['taskId'] == 'material-001'
    assert work['images'] == 3 and work['remainingImages'] == 4
    first = read_object(absolute(work, work['navigation']['currentImages']))
    facts_pointer = work['navigation']['facts']
    for image in first['entries']:
        assert absolute(work, image['framePath']).is_file()
    batch = copy.deepcopy(first['observationsTemplate'])
    for entry in batch['observations']:
        entry.update(visualObservation=OBSERVATIONS[entry['sourceFrameRef']], standardCategory='other')
    work = invoke(tmp_path, '--observations-json', batch)
    assert work['navigation']['facts'] == facts_pointer
    assert work['images'] == work['remainingImages'] == 1
    assert invoke(tmp_path, '--observations-json', batch) == work
    last = read_object(absolute(work, work['navigation']['currentImages']))
    last_batch = copy.deepcopy(last['observationsTemplate'])
    for entry in last_batch['observations']:
        entry.update(visualObservation=OBSERVATIONS[entry['sourceFrameRef']], standardCategory='other')
    material = copy.deepcopy(authored[3][1])
    for frame in material['materialAnalyses'][0]['highlightAnalyses']:
        frame.pop('visualObservation')
    work = invoke(tmp_path, '--submission-json', request(work, material, frameObservations=last_batch))
    assert work['taskId'] == 'chapter04'
    assert work['images'] == 0 and work['navigation']['currentImages'] is None
    visuals = pages(indexed_pages(work, 'facts'))['reviewedVisuals']
    assert len(visuals['images']) == 4 and visuals['frameCount'] == 6
    assert all(image['standardCategory'] == 'other' for image in visuals['images'])
    template = read_object(absolute(work, work['navigation']['submissionTemplate']))
    assert [g['category'] for g in template['part']['highlightDashboard']['groups']] == ['other']
    part = copy.deepcopy(authored[4][1])
    part['highlightDashboard']['groups'][0].pop('frameRefs')
    done = invoke(tmp_path, '--submission-json', request(work, part))
    assert done['status'] == 'ready_for_validation'
    assert done['finalValidationRequired'] and not done['contentReviewed']
    candidate = absolute(done, done['analysisJson'])
    result = read_object(candidate)
    assert result['materialAnalyses'] == authored[3][1]['materialAnalyses']
    assert result['highlightDashboard'] == authored[4][1]['highlightDashboard']
    assert verify_assembly(candidate, source)
    assert invoke(tmp_path) == done


def test_legacy_cli_work_item_is_still_explicitly_available(tmp_path):
    synthetic_inputs(tmp_path)
    work = invoke(tmp_path, legacy=True)
    assert work['taskId'] == 'chapter01'
    assert 'factsPages' in work and 'navigation' not in work


def test_navigation_capacity_failure_saves_current_pointer_and_legacy_retry_does_not_repeat_commit(tmp_path, monkeypatch):
    root = synthetic_inputs(tmp_path)
    first = analysis_session.advance_session(root)
    part = authored_parts(inspect_inputs(root)['manifestSha256'])[0][1]
    payload = request(first, part)
    target = root / 'capacity-request.json'
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    # The real publisher's capacity boundary must not roll back an accepted task.
    monkeypatch.setattr(analysis_navigation, 'BYTE_LIMIT', 100)
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(['analysis-session', '--output-dir', str(tmp_path), '--submission-json', str(target)])
    assert code == 2
    state = read_object(root / 'analysis-session/state.json')
    assert [entry['taskId'] for entry in state['completed']] == ['chapter01']
    assert read_object(root / 'analysis-session/resume.json')['taskId'] == 'chapter02'
    recovered = invoke(tmp_path, '--submission-json', payload, legacy=True)
    assert recovered['taskId'] == 'chapter02'
    assert read_object(root / 'analysis-session/state.json') == state


def test_cached_material_navigation_maps_the_observation_to_current_material_frames(tmp_path, monkeypatch):
    from test_frame_review import inputs
    from test_analysis_session import only_materials, material, submit, write
    from test_analysis_visual_reuse import omit_observations
    root = inputs(tmp_path, [[b'a'], [b'a', b'a']])
    only_materials(monkeypatch)
    work = analysis_session.advance_session(root, navigation=True)
    group = read_object(absolute(work, work['navigation']['currentImages']))
    assert group['entries'][0]['frameRefs'] == ['highlight:m-1:peak_0']
    batch = group['observationsTemplate']
    batch['observations'][0].update(visualObservation='画面中是一只瓶子。', standardCategory='product')
    part = omit_observations(material('m-1', ['highlight:m-1:peak_0']))
    second = analysis_session.advance_session(root, navigation=True,
        submission_json=submit(root, work, part, frameObservations=batch))
    assert second['images'] == 0 and second['navigation']['currentImages'] is None
    cached = read_object(absolute(second, second['navigation']['cachedObservations']))['entries']
    assert len(cached) == 1
    assert cached[0]['sourceFrameRef'] == 'highlight:m-1:peak_0'
    assert cached[0]['frameRefs'] == ['highlight:m-2:peak_0', 'highlight:m-2:peak_1']
