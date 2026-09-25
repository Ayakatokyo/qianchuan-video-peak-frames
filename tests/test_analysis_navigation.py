"""Stable control files keep fact pages intact and expose one image group."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import analysis_navigation as navigation
from analysis_session import _write_pages
from analysis_task_io import pretty_json, restore_task_pages


def read(path):
    return json.loads(Path(path).read_text())


def sample(tmp_path, *, count=6):
    root = tmp_path / 'diagnostic-analysis'
    directory = root / 'analysis-session'
    directory.mkdir(parents=True)
    facts = {'materials': [{'name': '客户产品', 'metric': i, 'evidenceRef': f'frame:{i}'} for i in range(90)]}
    contract = {'allowedCategories': ['portrait', 'product', 'other']}
    work = {'status': 'in_progress', 'inputSha256': 'a' * 64, 'taskId': 'material-001',
            'taskCount': 5, 'completedTaskCount': 3, 'contentReviewed': False,
            'finalValidationRequired': True, 'sessionDir': str(directory),
            'contextHandoff': {'resumePath': str(directory / 'resume.json'), 'canReleaseCompletedWork': True},
            'factsPages': _write_pages(directory, 'material-001', 'facts', facts, 'a' * 64),
            'contractPages': _write_pages(directory, 'material-001', 'contract', contract, 'a' * 64)}
    template = directory / 'work/material-001/submission-template.json'
    template.write_text(json.dumps({'inputSha256': work['inputSha256'], 'taskId': work['taskId'], 'part': {}}))
    work['submissionTemplatePath'] = str(template)
    images = [{'frameRef': f'frame:{i}', 'sha256': hashlib.sha256(str(i).encode()).hexdigest(),
               'framePath': str(tmp_path / 'peak-frames' / f'{i}.jpg'), 'materialId': 'm1'} for i in range(count)]
    refs = [entry['frameRef'] for entry in images]
    work['frameReview'] = {'status': 'in_progress', 'materialId': 'm1', 'inputIndexSha256': 'b' * 64,
        'frameRefs': refs, 'imagesToView': images, 'cachedObservations': {},
        'visionBatches': [{'imagesToView': images[:3]}],
        'checkpointTemplate': {'inputIndexSha256': 'b' * 64, 'frameRefs': refs, 'observations': []}}
    return root, directory, work, facts


def index_entries(root, first):
    result = []
    while first:
        page = read(root / first)
        assert isinstance(page['entries'], list)
        result.extend(page['entries'])
        first = page['nextPage']
    return result


def test_navigation_has_fixed_shape_first_three_images_and_full_fact_coverage(tmp_path):
    root, directory, work, facts = sample(tmp_path)
    work['visualAssemblyRule'] = 'Only the saved visual observation is filled by the program.'
    work['readingRule'] = 'Read every page. Truncation is not reading.'
    before = copy.deepcopy(work)
    result = navigation.publish_navigation(directory, work)
    assert set(result['navigation']) == {'basePath', 'facts', 'contract', 'references',
        'submissionTemplate', 'currentImages', 'cachedObservations'}
    assert result['navigation']['basePath'] == str(root)
    assert result['visualAssemblyRule'] == work['visualAssemblyRule']
    assert work['readingRule'] in result['readingRule']
    assert result['images'] == 3 and result['remainingImages'] == 6
    assert not {'frameReview', 'factsPages', 'contractPages', 'workItemPages'} & set(result)
    group = read(root / result['navigation']['currentImages'])
    assert len(group['entries']) == 3
    assert group['inputSha256'] == work['inputSha256']
    assert group['inputIndexSha256'] == work['frameReview']['inputIndexSha256']
    for entry, original in zip(group['entries'], work['frameReview']['imagesToView']):
        assert entry['sha256'] == original['sha256'] and entry['frameRef'] == original['frameRef']
        assert (root / entry['framePath']).resolve() == Path(original['framePath']).resolve()
    assert group['nextPage'] is None
    assert group['observationsTemplate'] == {'inputIndexSha256': 'b' * 64,
        'frameRefs': work['frameReview']['frameRefs'], 'observations': [
            {'sourceFrameRef': f'frame:{i}', 'visualObservation': None, 'standardCategory': None} for i in range(3)]}
    assert all(not Path(entry['framePath']).is_absolute() for entry in group['entries'])
    pages = index_entries(root, result['navigation']['facts'])
    assert [item['sha256'] for item in pages] == [item['sha256'] for item in work['factsPages']]
    assert restore_task_pages([read(root / entry['path'])['records'] for entry in pages]) == facts
    assert work == before


def test_stash_keeps_facts_pointer_and_only_moves_image_group(tmp_path):
    root, directory, work, _ = sample(tmp_path)
    first = navigation.publish_navigation(directory, work)
    facts = root / first['navigation']['facts']
    stat = facts.stat().st_mtime_ns
    later = copy.deepcopy(work)
    later['frameReview']['imagesToView'] = work['frameReview']['imagesToView'][3:]
    later['frameReview']['cachedObservations'] = {entry['sha256']: {
        'sourceFrameRef': entry['frameRef'], 'visualObservation': '真实画面观察。', 'standardCategory': 'product'}
        for entry in work['frameReview']['imagesToView'][:3]}
    second = navigation.publish_navigation(directory, later)
    assert second['navigation']['facts'] == first['navigation']['facts']
    assert facts.stat().st_mtime_ns == stat
    assert second['navigation']['currentImages'] != first['navigation']['currentImages']
    assert [entry['frameRef'] for entry in read(root / second['navigation']['currentImages'])['entries']] == ['frame:3', 'frame:4', 'frame:5']
    assert len(index_entries(root, second['navigation']['cachedObservations'])) == 3
    assert navigation.publish_navigation(directory, later) == second


def test_full_cache_and_validation_ready_have_no_image_work(tmp_path):
    root, directory, work, _ = sample(tmp_path, count=0)
    work['frameReview']['cachedObservations'] = {'c' * 64: {'sourceFrameRef': 'frame:source', 'visualObservation': '原有观察'}}
    result = navigation.publish_navigation(directory, work)
    assert result['images'] == result['remainingImages'] == 0
    assert result['navigation']['currentImages'] is None
    assert result['navigation']['cachedObservations'] is not None
    assert '无需' in result['readingRule']
    finished = {key: value for key, value in work.items() if key not in
        {'factsPages', 'contractPages', 'frameReview', 'submissionTemplatePath'}}
    finished.update(status='ready_for_validation', taskId=None, analysisJson=str(root / 'candidate.json'))
    final = navigation.publish_navigation(directory, finished)
    assert final['images'] == 0 and final['analysisJson'] == 'candidate.json'
    assert (Path(final['navigation']['basePath']) / final['analysisJson']).resolve() == root / 'candidate.json'
    assert all(value is None for key, value in final['navigation'].items() if key != 'basePath')


def test_long_chinese_paths_and_many_index_pages_stay_bounded(tmp_path):
    long = tmp_path / ('中文客户' * 14) / ('中文运行' * 14)
    root, directory, work, _ = sample(long)
    work['factsPages'] = work['factsPages'] * 80
    work['frameReview']['cachedObservations'] = {f'{index:064x}': {
        'sourceFrameRef': f'frame:{index}', 'visualObservation': '真实可见画面要点。' * 12,
        'standardCategory': 'product'} for index in range(140)}
    result = navigation.publish_navigation(directory, work)
    assert len(index_entries(root, result['navigation']['facts'])) == len(work['factsPages'])
    cache = index_entries(root, result['navigation']['cachedObservations'])
    assert {entry['sha256']: {key: value for key, value in entry.items() if key not in ('sha256', 'frameRefs')}
            for entry in cache} == work['frameReview']['cachedObservations']
    assert len((pretty_json(result) + '\n').encode()) <= 8000
    for path in (directory / 'navigation').rglob('*.json'):
        raw = path.read_bytes()
        assert len(raw) <= 8000
        assert b'"$table"' not in raw
        payload = read(path)
        if 'entries' in payload:
            assert isinstance(payload['entries'], list)
            assert payload['nextPage'] is None or isinstance(payload['nextPage'], str)


def test_rejects_symlinked_output_and_changed_fact_file(tmp_path):
    root, directory, work, _ = sample(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (directory / 'navigation').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symbolic'):
        navigation.publish_navigation(directory, work)
    assert list(outside.iterdir()) == []
    (directory / 'navigation').unlink()
    Path(work['factsPages'][0]['path']).write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        navigation.publish_navigation(directory, work)


def test_interrupted_publication_can_be_retried(tmp_path, monkeypatch):
    root, directory, work, _ = sample(tmp_path)
    original = navigation.os.replace
    writes = []
    def interrupt(source, target):
        writes.append(target)
        if len(writes) == 2:
            raise OSError('interrupted')
        return original(source, target)
    monkeypatch.setattr(navigation.os, 'replace', interrupt)
    with pytest.raises(OSError, match='interrupted'):
        navigation.publish_navigation(directory, work)
    monkeypatch.setattr(navigation.os, 'replace', original)
    result = navigation.publish_navigation(directory, work)
    assert result['images'] == 3
    assert not list((directory / 'navigation').rglob('*.tmp'))


def test_oversized_submission_template_fails_without_silent_truncation(tmp_path):
    _, directory, work, _ = sample(tmp_path)
    Path(work['submissionTemplatePath']).write_text(json.dumps({'part': '中文' * 4000}, ensure_ascii=False))
    with pytest.raises(navigation.NavigationCapacityError, match='submission template.*8000'):
        navigation.publish_navigation(directory, work)


@pytest.mark.parametrize('oversized', ['image_group', 'cache_entry', 'response'])
def test_only_reading_capacity_errors_can_use_legacy_fallback(tmp_path, oversized):
    _, directory, work, _ = sample(tmp_path)
    if oversized == 'image_group':
        work['frameReview']['imagesToView'][0]['framePath'] = 'image-' + 'a' * 9000
    elif oversized == 'cache_entry':
        work['frameReview']['cachedObservations'] = {'c' * 64: {
            'sourceFrameRef': 'frame:source', 'visualObservation': '中文' * 4000}}
    else:
        work['visualAssemblyRule'] = '中文' * 4000
    with pytest.raises(navigation.NavigationCapacityError, match='8000'):
        navigation.publish_navigation(directory, work)


def test_corrupted_source_is_not_a_capacity_fallback(tmp_path):
    _, directory, work, _ = sample(tmp_path)
    Path(work['factsPages'][0]['path']).write_text('{}')
    with pytest.raises(ValueError) as caught:
        navigation.publish_navigation(directory, work)
    assert not isinstance(caught.value, navigation.NavigationCapacityError)


def test_valid_source_over_reading_budget_is_a_capacity_error(tmp_path, monkeypatch):
    _, directory, work, _ = sample(tmp_path)
    monkeypatch.setattr(navigation, 'BYTE_LIMIT', 100)
    with pytest.raises(navigation.NavigationCapacityError, match='source page'):
        navigation.publish_navigation(directory, work)
