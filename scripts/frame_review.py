"""Frozen JPEG inputs and atomic, material-scoped review checkpoints.

Only the current model supplies observations; this module never infers image content.
"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from analysis_handoff import file_hash, inspect_inputs, read_object
from batch_runtime import acquire_run_lock, write_json_atomically

DEFAULT_REVIEW_BATCH_SIZE = 6
LEGACY_REVIEW_BATCH_SIZE = 6
VISION_BATCH_SIZE = 3


def now():
    return datetime.now(timezone.utc).isoformat()


def _batches(inputs, batch_size=DEFAULT_REVIEW_BATCH_SIZE):
    batches = []
    for mid in inputs['materialIds']:
        group, hashes = [], set()
        for frame in inputs['frames']:
            if frame['materialId'] != mid:
                continue
            if frame['sha256'] not in hashes and len(hashes) == batch_size:
                batches.append({'materialId': mid, 'frameRefs': group})
                group, hashes = [], set()
            group.append(frame['frameRef'])
            hashes.add(frame['sha256'])
        if group:
            batches.append({'materialId': mid, 'frameRefs': group})
    return batches


def _state(inputs, index_hash, cache=None):
    cache = cache or {}
    return {'schemaVersion': 2, 'status': 'in_progress',
            'inputIndexSha256': index_hash,
            'analysisUnitManifestPath': 'analysis-units/analysis-unit-manifest.json',
            'promptPath': 'diagnostic-analysis-prompt.md',
            'uniqueImageCount': inputs['uniqueImageCount'],
            'batchSize': DEFAULT_REVIEW_BATCH_SIZE, 'reviewBatchCount': 0,
            'visualObservationCache': cache, 'stashedFrameRefs': [], 'frameRefsBySha256': {},
            'completedFrameRefs': [], 'pendingFrameRefs': [f['frameRef'] for f in inputs['frames']],
            'batches': _batches(inputs), 'checkpoints': []}


def _advance(state):
    pending = set(state['pendingFrameRefs'])
    batch = next((b for b in state['batches'] if pending.intersection(b['frameRefs'])), None)
    state['currentMaterialId'] = batch['materialId'] if batch else None
    state['currentBatchFrameRefs'] = batch['frameRefs'] if batch else []
    if batch is None:
        state['status'] = 'complete'
        state.setdefault('completedAt', now())


def _load(analysis_dir, *, _context=None):
    root = Path(analysis_dir)
    index = read_object(root / 'analysis-input-index.json')
    current = inspect_inputs(root) if _context is None else _context[0]
    if index != current:
        raise ValueError('frozen input index changed; rebuild frame review before continuing')
    state = read_object(root / 'frame-review-progress.json')
    if state.get('inputIndexSha256') != file_hash(root / 'analysis-input-index.json'):
        raise ValueError('frame review input index hash mismatch; rebuild required')
    _validate_state(index, state)
    return index, state


def _validate_state(index, state):
    refs = {f['frameRef']: f for f in index['frames']}
    completed, pending = state.get('completedFrameRefs', []), state.get('pendingFrameRefs', [])
    if (len(completed) != len(set(completed)) or len(pending) != len(set(pending))
            or set(completed) & set(pending) or set(completed) | set(pending) != set(refs)):
        raise ValueError('frame review coverage mismatch')
    batch_size = state.get('batchSize', LEGACY_REVIEW_BATCH_SIZE)
    if batch_size not in {4, 6}:
        raise ValueError('frame review batch size is unsupported')
    if state.get('batches') != _batches(index, batch_size):
        raise ValueError('frame review batch plan mismatch')
    cache = state.get('visualObservationCache', {})
    for sha, entry in cache.items():
        source = refs.get(entry.get('sourceFrameRef')) if isinstance(entry, dict) else None
        if not source or source['sha256'] != sha or not isinstance(entry.get('visualObservation'), str) or not entry['visualObservation'].strip():
            raise ValueError('invalid cached visual observation')
        if set(entry) != {'sourceFrameRef', 'visualObservation'}:
            raise ValueError('cache only supports visual observations')
    mapping = {}
    for ref in completed:
        sha = refs[ref]['sha256']
        if sha not in cache:
            raise ValueError('completed frame has no visual observation')
        mapping.setdefault(sha, []).append(ref)
    if state.get('frameRefsBySha256') != mapping:
        raise ValueError('frame review SHA mapping mismatch')
    count = state.get('reviewBatchCount')
    checkpoints = state.get('checkpoints', [])
    if count != len(checkpoints) or count > len(state['batches']):
        raise ValueError('checkpoint count mismatch')
    expected = [r for b in state['batches'][:count] for r in b['frameRefs']]
    if completed != expected or any(c.get('frameRefs') != b['frameRefs'] for c, b in zip(checkpoints, state['batches'])):
        raise ValueError('checkpoint coverage mismatch')
    batch = state['batches'][count] if count < len(state['batches']) else None
    if state.get('currentBatchFrameRefs') != (batch['frameRefs'] if batch else []) or state.get('currentMaterialId') != (batch['materialId'] if batch else None):
        raise ValueError('current batch mismatch')
    stashed = state.get('stashedFrameRefs', [])
    if (not isinstance(stashed, list) or any(not isinstance(ref, str) for ref in stashed)
            or len(stashed) != len(set(stashed))
            or any(ref not in state['currentBatchFrameRefs'] for ref in stashed)):
        raise ValueError('stashed observations must belong to the current frozen batch')
    for ref in stashed:
        if cache.get(refs[ref]['sha256'], {}).get('sourceFrameRef') != ref:
            raise ValueError('stashed observation cache mismatch')
    if (state.get('uniqueImageCount') != index['uniqueImageCount']
            or state.get('status') != ('in_progress' if pending else 'complete')
            or (not pending and not state.get('completedAt'))):
        raise ValueError('frame review completion state mismatch')


def start_frame_review(analysis_dir, *, rebuild=False, _context=None):
    root = Path(analysis_dir)
    with acquire_run_lock(root / '.frame-review.lock', root / 'analysis-input-index.json', reclaim_stale=True):
        index_path, progress_path = root / 'analysis-input-index.json', root / 'frame-review-progress.json'
        if progress_path.exists() and not rebuild:
            index, state = _load(root, _context=_context)
            return _review_status(index, state)
        inputs = inspect_inputs(root) if _context is None else _context[0]
        cache = {}
        if progress_path.exists():
            # Preserve the old ledger before a new epoch; only unchanged inputs can
            # donate observations. New paths/hashes must be viewed again.
            old = read_object(progress_path)
            old_index = read_object(index_path) if index_path.exists() else {}
            try:
                if not index_path.exists() or old.get("inputIndexSha256") != file_hash(index_path):
                    raise ValueError("old input index is not bound to the ledger")
                _validate_state(old_index, old)
            except (ValueError, KeyError, TypeError):
                pass  # Legacy/incomplete notes cannot certify reusable observations.
            else:
                old_frames = {f['frameRef']: f for f in old_index['frames']}
                new_frames = {f['frameRef']: f for f in inputs['frames']}
                for sha, entry in old['visualObservationCache'].items():
                    ref = entry['sourceFrameRef']
                    if old_frames.get(ref) == new_frames.get(ref):
                        cache[sha] = entry
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
            write_json_atomically(root / f'frame-review-progress.{stamp}.json', old)
            if old_index:
                write_json_atomically(root / f'analysis-input-index.{stamp}.json', old_index)
        write_json_atomically(index_path, inputs)
        state = _state(inputs, file_hash(index_path), cache)
        _advance(state)
        write_json_atomically(progress_path, state)
        return _review_status(inputs, state)


def review_status(analysis_dir, *, _context=None):
    return _review_status(*_load(analysis_dir, _context=_context))


def _review_status(index, state):
    frames = {f['frameRef']: f for f in index['frames']}
    unique, reused = {}, {}
    for ref in state['currentBatchFrameRefs']:
        frame = frames[ref]
        if frame['sha256'] not in state['visualObservationCache']:
            unique.setdefault(frame['sha256'], frame)
        else:
            reused[frame['sha256']] = state['visualObservationCache'][frame['sha256']]
    images = list(unique.values())
    stashed_refs = set(state.get('stashedFrameRefs', []))

    def template(group):
        return {'inputIndexSha256': state['inputIndexSha256'],
                'frameRefs': list(state['currentBatchFrameRefs']),
                'observations': [{'sourceFrameRef': f['frameRef'], 'visualObservation': None}
                                 for f in group]}

    return {'status': state['status'], 'inputIndexSha256': state['inputIndexSha256'],
            'reviewBatchCount': state['reviewBatchCount'], 'uniqueImageCount': state['uniqueImageCount'],
            'completedCount': len(state['completedFrameRefs']), 'pendingCount': len(state['pendingFrameRefs']),
            'materialId': state['currentMaterialId'], 'frameRefs': state['currentBatchFrameRefs'],
            'imagesToView': images, 'cachedObservations': reused,
            'stashedObservations': {sha: entry for sha, entry in reused.items()
                                   if entry['sourceFrameRef'] in stashed_refs},
            'visionBatches': [{'imagesToView': images[i:i + VISION_BATCH_SIZE],
                              'stashTemplate': template(images[i:i + VISION_BATCH_SIZE])}
                             for i in range(0, len(images), VISION_BATCH_SIZE)],
            'observationGuidance': ('每次实际查看最多 3 张唯一 JPEG，每图默认记录 25–60 字真实可见要点；'
                                    '查看后立即暂存，仅实际 JPEG SHA-256 相同时复用观察。'
                                    '暂存不完成批次，全部图片有观察后再提交 checkpoint。'),
            'taskId': (f"material-{index['materialIds'].index(state['currentMaterialId']) + 1:03d}"
                       if state['currentMaterialId'] else None),
            'checkpointTemplate': template(images) if state['currentMaterialId'] else None}


def _prepare_frame_observations(index, original, batch, *, complete=False):
    """Validate a proposed transition without modifying the ledger or its input."""
    _validate_state(index, original)
    if not isinstance(batch, dict) or set(batch) != {'inputIndexSha256', 'frameRefs', 'observations'}:
        raise ValueError('checkpoint only accepts index hash, frameRefs and observations')
    if (batch['inputIndexSha256'] != original['inputIndexSha256']
            or batch['frameRefs'] != original['currentBatchFrameRefs']
            or not original['currentBatchFrameRefs']):
        raise ValueError('checkpoint must cover exactly the current frozen batch')
    observations = batch['observations']
    if not isinstance(observations, list):
        raise ValueError('observations must be a list')
    state = deepcopy(original)
    frames = {f['frameRef']: f for f in index['frames']}
    stashed = state.setdefault('stashedFrameRefs', [])
    for entry in observations:
        if not isinstance(entry, dict) or set(entry) != {'sourceFrameRef', 'visualObservation'}:
            raise ValueError('only sourceFrameRef and visualObservation may be cached')
        ref, observation = entry['sourceFrameRef'], entry['visualObservation']
        if not isinstance(ref, str) or ref not in state['currentBatchFrameRefs']:
            raise ValueError('observation must belong to the current material batch')
        if not isinstance(observation, str) or not observation.strip():
            raise ValueError('visualObservation must be non-empty text')
        sha = frames[ref]['sha256']
        if sha in state['visualObservationCache']:
            if state['visualObservationCache'][sha] != entry:
                raise ValueError('conflicting SHA observation; reuse the existing cache')
            continue  # Identical retries do not replace already durable observations.
        state['visualObservationCache'][sha] = deepcopy(entry)
        stashed.append(ref)
    if complete:
        for ref in state['currentBatchFrameRefs']:
            sha = frames[ref]['sha256']
            if sha not in state['visualObservationCache']:
                raise ValueError('batch has unreviewed images; checkpoint was not written')
            state['frameRefsBySha256'].setdefault(sha, []).append(ref)
            state['completedFrameRefs'].append(ref)
            state['pendingFrameRefs'].remove(ref)
        state['checkpoints'].append({'frameRefs': state['currentBatchFrameRefs'],
                                     'viewedImageCount': len(stashed),
                                     'cacheHitCount': len(state['currentBatchFrameRefs']) - len(stashed),
                                     'completedAt': now()})
        state['reviewBatchCount'] += 1
        state['stashedFrameRefs'] = []
        _advance(state)
    _validate_state(index, state)
    return state


def _save_frame_observations(analysis_dir, batch, *, complete, _context=None):
    root = Path(analysis_dir)
    with acquire_run_lock(root / '.frame-review.lock', root / 'analysis-input-index.json', reclaim_stale=True):
        index, original = _load(root, _context=_context)
        state = _prepare_frame_observations(index, original, batch, complete=complete)
        if state != original:
            write_json_atomically(root / 'frame-review-progress.json', state)
        return _review_status(index, state)


def stash_frame_observations(analysis_dir, batch, *, _context=None):
    """Persist only supplied, model-authored observations; leave coverage pending."""
    return _save_frame_observations(analysis_dir, batch, complete=False, _context=_context)


def checkpoint_frame_review(analysis_dir, batch, *, _context=None):
    return _save_frame_observations(analysis_dir, batch, complete=True, _context=_context)


def validate_frame_review(analysis_dir):
    root = Path(analysis_dir)
    index_path = root / 'analysis-input-index.json'
    progress_path = root / 'frame-review-progress.json'
    if not any(p.exists() and read_object(p).get('schemaVersion') == 2 for p in (index_path, progress_path)):
        return None  # Historical runs retain their pre-ledger validation contract.
    _, state = _load(root)
    if state['status'] != 'complete':
        raise ValueError('frame review is incomplete; finish pending batches')
    return file_hash(root / 'frame-review-progress.json')
