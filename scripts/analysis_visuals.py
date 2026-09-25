"""Copy model-authored visual facts into report parts; never infer prose or labels."""
from copy import deepcopy
import hashlib


def material_observations(part, index, ledger):
    """Expand omitted observations from the exact, input-bound image SHA cache."""
    result = deepcopy(part)
    frames = {frame['frameRef']: frame for frame in index['frames']}
    copies = []
    for item in result.get('materialAnalyses', []):
        for entry in item.get('highlightAnalyses', []):
            if not isinstance(entry, dict) or 'visualObservation' in entry:
                continue  # Legacy authored parts retain their supplied text.
            frame = frames.get(entry.get('frameRef'))
            if frame is None or frame['materialId'] != item.get('materialId'):
                raise ValueError('observation reference does not belong to this material')
            cached = ledger['visualObservationCache'].get(frame['sha256'])
            if cached is None:
                raise ValueError('material has unreviewed images; cannot copy an observation')
            entry['visualObservation'] = cached['visualObservation']
            copies.append({'frameRef': frame['frameRef'], 'imageSha256': frame['sha256'],
                           'sourceFrameRef': cached['sourceFrameRef'],
                           'observationSha256': hashlib.sha256(cached['visualObservation'].encode()).hexdigest()})
    return result, {'inputIndexSha256': ledger['inputIndexSha256'], 'observationCopies': copies}


def standard_groups(index, ledger):
    """None means legacy labels are missing; an empty list means no frames exist."""
    groups = {category: [] for category in ('portrait', 'product', 'other')}
    for frame in index['frames']:
        category = ledger['visualObservationCache'].get(frame['sha256'], {}).get('standardCategory')
        if category not in groups:
            return None
        groups[category].append(frame['frameRef'])
    return [{'category': category, 'frameRefs': refs} for category, refs in groups.items() if refs]


def chapter_categories(part, index, ledger):
    """Expand model-selected standard labels; all group prose stays model-authored."""
    result = deepcopy(part)
    available = standard_groups(index, ledger)
    by_category = {g['category']: g['frameRefs'] for g in available or []}
    recorded = {frame['frameRef']: ledger['visualObservationCache'].get(frame['sha256'], {}).get('standardCategory')
                for frame in index['frames']}
    copies = []
    for group in result.get('highlightDashboard', {}).get('groups', []):
        if not isinstance(group, dict):
            continue  # The report validator owns the general shape checks.
        if 'frameRefs' not in group:
            if available is None:
                raise ValueError('standardCategory is missing in legacy observations; supply explicit frameRefs')
            category = group.get('category')
            if category not in by_category:
                raise ValueError('category has no observed images; use the supplied nonempty standard groups')
            group['frameRefs'] = list(by_category[category])
            copies.append({'category': category, 'frameRefs': list(group['frameRefs'])})
        if isinstance(group.get('frameRefs'), list):
            for ref in group['frameRefs']:
                if isinstance(ref, str) and recorded.get(ref) is not None and recorded[ref] != group.get('category'):
                    raise ValueError('standard category frameRefs conflict with saved model labels')
    return result, {'inputIndexSha256': ledger['inputIndexSha256'], 'standardGroupCopies': copies}
