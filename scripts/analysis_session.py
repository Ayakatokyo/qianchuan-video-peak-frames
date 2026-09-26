"""Small durable work items for the current model, never generated analysis prose.

One source snapshot per step binds facts, JPEGs, observations and authored parts.
The host owns context compaction; this module provides the recovery pointers.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import time

from analysis_handoff import assemble_analysis, check_analysis_part, file_hash, read_object
from analysis_reader import json_text
from analysis_task_io import pretty_json
from analysis_tasks import _packets, _pages
from batch_runtime import acquire_run_lock, write_json_atomically
import frame_review


def _digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def _publish(path, value):
    """Keep published authoring artifacts immutable, including interrupted writes."""
    if path.is_symlink():
        raise ValueError('session artifact must not be symbolic')
    if path.exists():
        if read_object(path) != value:
            raise ValueError(f'session artifact changed: {path.name}')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomically(path, value)


def _request(root, path):
    path = Path(path)
    if path.is_symlink() or not path.resolve().is_relative_to(root) or path.suffix != '.json':
        raise ValueError('submission must be JSON within diagnostic-analysis')
    return read_object(path)


def _load_state(directory, binding, packets):
    path = directory / 'state.json'
    task_ids = [task['taskId'] for task, _, _ in packets]
    if not path.exists():
        return {'schemaVersion': 1, 'inputSha256': binding, 'taskIds': task_ids,
                'completed': [], 'taskStartedAtSec': time.time()}
    state = read_object(path)
    if state.get('inputSha256') != binding or state.get('taskIds') != task_ids:
        raise ValueError('analysis session inputs or implementation changed; preserve this session and use a new session directory')
    completed = state.get('completed')
    if (not isinstance(completed, list) or len(completed) > len(task_ids)
            or [entry.get('taskId') for entry in completed] != task_ids[:len(completed)]):
        raise ValueError('analysis session task coverage mismatch')
    for entry in completed:
        expected = f"parts/{entry['taskId']}.json"
        path = directory / expected
        if (entry.get('partPath') != expected or path.is_symlink() or not path.is_file()
                or file_hash(path) != entry.get('partSha256')):
            raise ValueError('submitted part changed; preserve sources and start a new session')
    return state


def _check_slot(task, part):
    fields = set(part) - {'schemaVersion'}
    task_id = task['taskId']
    allowed = {
        'chapter01': {'analysisProvenance', 'overallConclusion', 'portfolioDimensionConclusions', 'hotMaterialInsight'},
        'chapter02': {'batchInsights'},
        'chapter03': {'batchInsights', 'keywordCloudInsight', 'keywordCloudReview'},
        'chapter04': {'highlightDashboard', 'customClassification'},
    }.get(task_id, {'materialAnalyses'})
    required = allowed - ({'hotMaterialInsight'} if task_id == 'chapter01' else set())
    if not required <= fields or fields - allowed:
        raise ValueError(f'{task_id} part fields must cover ' + ', '.join(sorted(required)))
    if task_id in {'chapter02', 'chapter03'}:
        topic = 'audience' if task_id == 'chapter02' else 'script'
        if any(not isinstance(x, dict) or x.get('topic') != topic for x in part.get('batchInsights', [])):
            raise ValueError(f'{task_id} only accepts {topic} insights')
    if task_id.startswith('material-'):
        items = part.get('materialAnalyses')
        if not isinstance(items, list) or len(items) != 1 or items[0].get('materialId') != task['materialId']:
            raise ValueError('part must contain exactly the current material')


def _finish_cached_batches(root, mid, context):
    status = frame_review.start_frame_review(root, _context=context)
    while status['materialId'] == mid and not status['imagesToView']:
        status = frame_review.checkpoint_frame_review(root, {
            'inputIndexSha256': status['inputIndexSha256'],
            'frameRefs': status['frameRefs'], 'observations': []}, _context=context)
    return status


def _is_saved_batch(index, state, batch, mid):
    """An exact, already checkpointed observation request can be retried safely."""
    if (not isinstance(batch, dict)
            or set(batch) != {'inputIndexSha256', 'frameRefs', 'observations'}
            or batch['inputIndexSha256'] != state['inputIndexSha256']
            or not isinstance(batch['frameRefs'], list) or not batch['frameRefs']
            or not isinstance(batch['observations'], list)):
        raise ValueError('invalid frame observation payload')
    expected = {f['frameRef']: f for f in index['frames'] if f['materialId'] == mid}
    if any(not isinstance(ref, str) or ref not in expected for ref in batch['frameRefs']):
        raise ValueError('observations must belong to the current material')
    if batch['frameRefs'] not in [entry['frameRefs'] for entry in state['checkpoints']]:
        return False
    seen = set()
    for entry in batch['observations']:
        if (not isinstance(entry, dict) or set(entry) != {'sourceFrameRef', 'visualObservation'}
                or not isinstance(entry['sourceFrameRef'], str)
                or entry['sourceFrameRef'] not in batch['frameRefs']):
            raise ValueError('invalid replayed frame observation')
        sha = expected[entry['sourceFrameRef']]['sha256']
        if sha in seen or state['visualObservationCache'].get(sha) != entry:
            raise ValueError('replayed frame observation changed')
        seen.add(sha)
    return True


def _commit(root, directory, state, packets, context, payload):
    if set(payload) - {'inputSha256', 'taskId', 'part', 'frameObservations'}:
        raise ValueError('unknown submission fields')
    if payload.get('inputSha256') != state['inputSha256']:
        raise ValueError('submission input fingerprint changed')
    submission_sha = _digest(payload)
    old = next((x for x in state['completed'] if x['taskId'] == payload.get('taskId')), None)
    if old:
        if old['submissionSha256'] != submission_sha:
            raise ValueError('task already submitted with different content; preserve it and use a new session')
        return
    cursor = len(state['completed'])
    if cursor == len(packets) or payload.get('taskId') != packets[cursor][0]['taskId']:
        raise ValueError('submission must match the current task')
    task = packets[cursor][0]
    part = payload.get('part')
    if not isinstance(part, dict):
        raise ValueError('model-authored part must be an object')
    _check_slot(task, part)
    path = directory / 'parts' / f"{task['taskId']}.json"
    # The preflight's public contract hashes a real file. Stage only the supplied
    # JSON; it is not accepted or made visible as a completed task on failure.
    staging = directory / 'pending-part.json'
    write_json_atomically(staging, part)
    check_analysis_part(root, staging, _context=context, _part=part)
    mid = task.get('materialId')
    if mid is None and 'frameObservations' in payload:
        raise ValueError('chapter submissions cannot add frame observations')
    if mid is not None:
        index, original = frame_review._load(root, _context=context)
        proposed = original
        batch = payload.get('frameObservations')
        expected = {f['frameRef'] for f in index['frames'] if f['materialId'] == mid}
        if batch is not None:
            # A crash after the ledger write but before the session write may
            # leave this exact batch complete; compare every observation first.
            if not _is_saved_batch(index, original, batch, mid):
                proposed = frame_review._prepare_frame_observations(index, original, batch, complete=False)
        hashes = {f['frameRef']: f['sha256'] for f in index['frames']}
        if any(hashes[ref] not in proposed['visualObservationCache'] for ref in expected):
            raise ValueError('material has unreviewed images; stash real observations before submitting')
        # Validate everything above before any completion mutation. If the OS
        # exits between the following writes, an identical submission resumes.
        _publish(path, part)
        if batch is not None and proposed is not original:
            frame_review.stash_frame_observations(root, batch, _context=context)
        _finish_cached_batches(root, mid, context)
        _, reviewed = frame_review._load(root, _context=context)
        if not expected <= set(reviewed['completedFrameRefs']):
            raise ValueError('current material frame ledger is not complete')
    else:
        _publish(path, part)
    finished = time.time()
    state['completed'].append({'taskId': task['taskId'], 'partPath': str(path.relative_to(directory)),
        'partSha256': file_hash(path), 'submissionSha256': submission_sha,
        'completedAtSec': finished, 'elapsedSeconds': round(max(0, finished - state['taskStartedAtSec']), 3),
        'measurement': 'wall-clock from task activation to accepted submission; includes idle time'})
    state['taskStartedAtSec'] = finished
    write_json_atomically(directory / 'state.json', state)


def _write_pages(directory, task_id, section, packet, binding):
    pages = _pages(packet, binding, task_id, section)
    output = []
    for number, records in enumerate(pages):
        payload = {'inputSha256': binding, 'taskId': task_id, 'section': section,
            'page': number, 'pageCount': len(pages),
            'nextPage': number + 1 if number + 1 < len(pages) else None, 'records': records}
        path = directory / 'work' / task_id / f'{section}.{number:03d}.json'
        raw = (pretty_json(payload) + '\n').encode()
        if len(raw) > 8000:
            raise ValueError('session page exceeds reading budget')
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError('session page must not be symbolic')
        if path.exists() and path.read_bytes() != raw:
            raise ValueError('session page changed')
        if not path.exists():
            # Published pages are deterministic; a crash before rename is safe.
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(raw)
            temporary.replace(path)
        output.append({'path': str(path), 'byteSize': len(raw), 'sha256': file_hash(path)})
    return output


def _visual_digest(root, context):
    index, state = frame_review._load(root, _context=context)
    if state['status'] != 'complete':
        raise ValueError('visual synthesis requires complete frame review')
    return {'scope': 'complete frozen frame ledger; observations only, interpret each material separately',
        'images': [{'sha256': sha, **entry, 'frameRefs': state['frameRefsBySha256'][sha]}
                   for sha, entry in state['visualObservationCache'].items()
                   if sha in state['frameRefsBySha256']],
        'frameCount': len(index['frames'])}


def _work_item(root, directory, state, packets, context):
    cursor = len(state['completed'])
    result = {'status': 'in_progress', 'inputSha256': state['inputSha256'],
        'completedTaskCount': cursor, 'taskCount': len(packets), 'contentReviewed': False,
        'finalValidationRequired': True, 'sessionDir': str(directory)}
    result['contextHandoff'] = {'resumePath': str(directory / 'resume.json'),
        'canReleaseCompletedWork': bool(cursor),
        'instruction': 'Only keep this resume pointer, current task and unresolved questions. Completed facts/images/parts remain on disk. Host context compaction is optional and is not performed by this command.'}
    if cursor == len(packets):
        session_id = hashlib.sha256(str(directory.relative_to(root)).encode()).hexdigest()[:12]
        candidate = root / f'{directory.name}-{session_id}-candidate.json'
        paths = [directory / x['partPath'] for x in state['completed']]
        assemble_analysis(root, paths, candidate, _inputs=context[0], resume=True)
        result.update(status='ready_for_validation', taskId=None, analysisJson=str(candidate),
            nextAction='validate-analysis with review-template, then content review and finalize')
        return result
    task, _, packet = packets[cursor]
    task_id = task['taskId']
    facts = deepcopy(packet)
    facts.pop('outputSkeleton', None)
    facts.pop('allowedEvidenceRefs', None)
    contract = {k: deepcopy(v) for k, v in packet.items() if k not in {'scopedFacts', 'allowedEvidenceRefs'}}
    if task_id == 'chapter04':
        facts['reviewedVisuals'] = _visual_digest(root, context)
    result.update(taskId=task_id,
        factsPages=_write_pages(directory, task_id, 'facts', facts, state['inputSha256']),
        contractPages=_write_pages(directory, task_id, 'contract', contract, state['inputSha256']))
    # Chapter 01 has aggregate refs not all embedded in its projected facts.
    if task_id == 'chapter01':
        result['referencePages'] = _write_pages(directory, task_id, 'references',
            {'allowedEvidenceRefs': packet['allowedEvidenceRefs']}, state['inputSha256'])
    template = {'inputSha256': state['inputSha256'], 'taskId': task_id, 'part': packet['outputSkeleton']}
    template_path = directory / 'work' / task_id / 'submission-template.json'
    _publish(template_path, template)
    result['submissionTemplatePath'] = str(template_path)
    result['readingRule'] = 'Read every facts page for this task; contract once per structure. Truncation is not reading. Author part directly; no generated prose.'
    if task_id.startswith('material-'):
        review = frame_review.start_frame_review(root, _context=context)
        mid = task['materialId']
        index, ledger = frame_review._load(root, _context=context)
        refs = [f['frameRef'] for f in index['frames'] if f['materialId'] == mid]
        if review['materialId'] == mid:
            result['frameReview'] = review
        elif set(refs) <= set(ledger['completedFrameRefs']):
            hashes = {f['sha256'] for f in index['frames'] if f['materialId'] == mid}
            result['frameReview'] = {'status': 'material_reviewed', 'materialId': mid, 'frameRefs': refs,
                'imagesToView': [], 'visionBatches': [],
                'cachedObservations': {sha: ledger['visualObservationCache'][sha] for sha in hashes}}
        else:
            raise ValueError('frame ledger is behind current material; finish its earlier pending batch first')
    return result


def _bounded_work_item(directory, result):
    if len((pretty_json(result) + '\n').encode()) <= 8000:
        return result
    key = f"handoff-{result['taskId']}-{_digest(result)[:16]}"
    pages = _write_pages(directory, key, 'work-item', result, result['inputSha256'])
    compact = {k: result[k] for k in ('status', 'inputSha256', 'taskId', 'completedTaskCount',
        'taskCount', 'contentReviewed', 'finalValidationRequired', 'sessionDir', 'contextHandoff')}
    compact.update(workItemPages=pages,
        readingRule='Full work item is paginated to prevent truncation. Read all workItemPages, then its facts and current image group; all pages are at most 8000 UTF-8 bytes.')
    if len((pretty_json(compact) + '\n').encode()) > 8000:
        raise ValueError('work-item index exceeds reading budget; use a shorter session path')
    return compact


def advance_session(analysis_dir, *, session_dir=None, submission_json=None, observations_json=None):
    """Resume, stash at most one viewed image group, or accept one authored part."""
    root = Path(analysis_dir).resolve()
    directory = Path(session_dir).absolute() if session_dir else root / 'analysis-session'
    if directory.is_symlink() or not directory.resolve().is_relative_to(root) or directory.resolve() == root:
        raise ValueError('use a session subdirectory within diagnostic-analysis')
    directory = directory.resolve()
    if submission_json and observations_json:
        raise ValueError('use frameObservations in submission or stash observations separately')
    with acquire_run_lock(root / '.analysis-session.lock', root / 'analysis-input-index.json', reclaim_stale=True):
        units = {}
        inputs, binding, packets = _packets(root, section='all', loaded_units=units)
        binding = _digest({'tasks': binding, 'sessionImplementation': file_hash(Path(__file__))})
        context = (inputs, units)
        state = _load_state(directory, binding, packets)
        directory.mkdir(parents=True, exist_ok=True)
        if submission_json:
            _commit(root, directory, state, packets, context, _request(root, submission_json))
        elif observations_json:
            cursor = len(state['completed'])
            if cursor == len(packets) or not packets[cursor][0]['taskId'].startswith('material-'):
                raise ValueError('image observations require the current material task')
            status = frame_review.start_frame_review(root, _context=context)
            mid = packets[cursor][0]['materialId']
            index, ledger = frame_review._load(root, _context=context)
            batch = _request(root, observations_json)
            if not _is_saved_batch(index, ledger, batch, mid):
                if status['materialId'] != mid:
                    raise ValueError('observations must belong to the current material')
                frame_review.stash_frame_observations(root, batch, _context=context)
                _finish_cached_batches(root, mid, context)
        result = _bounded_work_item(directory, _work_item(root, directory, state, packets, context))
        write_json_atomically(directory / 'state.json', state)
        write_json_atomically(directory / 'resume.json', result)
        return result
