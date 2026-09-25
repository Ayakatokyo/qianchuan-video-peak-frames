"""Stable, bounded control files over unchanged lossless analysis fact pages.

Only navigation and model-supplied observation templates are published here.
Facts, image bytes and business analysis are never generated or abbreviated.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from analysis_task_io import pretty_json


BYTE_LIMIT = 8000
_BASIC_FIELDS = ('status', 'inputSha256', 'taskId', 'taskCount', 'completedTaskCount',
                 'contentReviewed', 'finalValidationRequired', 'sessionDir',
                 'contextHandoff', 'analysisJson', 'nextAction', 'visualAssemblyRule')


class NavigationCapacityError(ValueError):
    """The unchanged full work item is valid but cannot fit the reading budget."""


def _raw(value):
    return (pretty_json(value) + '\n').encode('utf-8')


def _relative(path, root):
    path = Path(path)
    return os.path.relpath(path, root) if path.is_absolute() else str(path)


def _reject_symlinks(path, root):
    """Check every owned component, including a dangling final symlink."""
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise ValueError('navigation artifact must remain within the session') from exc
    current = root
    for part in ('', *parts):
        current = current / part
        if current.is_symlink():
            raise ValueError('navigation artifact must not be symbolic')


def _publish(path, payload, directory):
    raw = _raw(payload)
    if len(raw) > BYTE_LIMIT:
        raise NavigationCapacityError('navigation artifact exceeds 8000 UTF-8 bytes; use legacy work item')
    _reject_symlinks(path, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlinks(path, directory)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('published navigation artifact changed; preserve this session')
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.stem + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _source(path, directory):
    path = Path(path)
    if not path.is_absolute():
        path = directory.parent / path
    path = Path(os.path.abspath(path))
    _reject_symlinks(path, directory)
    if not path.is_file():
        raise ValueError('navigation source artifact is missing')
    return path


def _page_index(directory, target, root, work, source_pages, section):
    entries = []
    for entry in source_pages:
        path = _source(entry['path'], directory)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256'] or len(raw) != entry['byteSize']:
            raise ValueError('navigation source page changed')
        payload = json.loads(raw)
        if (payload.get('inputSha256') != work['inputSha256']
                or payload.get('taskId') != work['taskId']
                or payload.get('section') != section):
            raise ValueError('navigation source page binding changed')
        if len(raw) > BYTE_LIMIT:
            raise NavigationCapacityError('navigation source page exceeds 8000 UTF-8 bytes; use legacy work item')
        entries.append({**entry, 'path': _relative(path, root)})
    return _entry_pages(directory, target, root, entries, {
        'format': 'analysis-page-index-v1', 'inputSha256': work['inputSha256'],
        'taskId': work['taskId'], 'section': section})


def _entry_pages(directory, target, root, entries, metadata):
    """Publish a plain entries list, with a relative nextPage on every page."""
    def page_path(number):
        return target.with_name(f'{target.name}.{number:03d}.json')

    # A pessimistic cursor leaves the same size budget on every page.
    envelope = {**metadata, 'nextPage': _relative(page_path(9999999999), root)}
    pages, page = [], []
    for entry in entries:
        if len(_raw({**envelope, 'entries': [entry]})) > BYTE_LIMIT:
            raise NavigationCapacityError('navigation entry exceeds 8000 UTF-8 bytes; use legacy work item')
        if page and len(_raw({**envelope, 'entries': [*page, entry]})) > BYTE_LIMIT:
            pages.append(page)
            page = []
        page.append(entry)
    pages.append(page)
    for number, records in enumerate(pages):
        next_page = _relative(page_path(number + 1), root) if number + 1 < len(pages) else None
        _publish(page_path(number), {**metadata, 'entries': records, 'nextPage': next_page}, directory)
    return _relative(page_path(0), root)


def _image_navigation(directory, target, root, work, review):
    images = deepcopy(review.get('imagesToView', [])[:3])
    current_refs = review.get('frameRefsBySha256', {})
    for frame in images:
        frame['framePath'] = _relative(frame['framePath'], root)
        frame['frameRefs'] = deepcopy(current_refs.get(frame['sha256'], [frame['frameRef']]))
    metadata = {'inputSha256': work['inputSha256'], 'taskId': work['taskId'],
                'inputIndexSha256': review.get('inputIndexSha256'), 'materialId': review.get('materialId')}
    cached = [{'sha256': sha, **deepcopy(entry), 'frameRefs': deepcopy(current_refs.get(sha, []))}
              for sha, entry in sorted(review.get('cachedObservations', {}).items())]
    cache_key = hashlib.sha256(_raw({**metadata, 'entries': cached})).hexdigest()[:16]
    cache_path = _entry_pages(directory, target / f'cached-observations-{cache_key}', root, cached,
                             {'format': 'cached-observations-v1', **metadata})
    group_path = None
    if images:
        group = {'format': 'current-image-group-v1', **metadata, 'entries': images, 'nextPage': None,
            'observationsTemplate': {'inputIndexSha256': review['inputIndexSha256'],
                'frameRefs': deepcopy(review['frameRefs']), 'observations': [
                    {'sourceFrameRef': frame['frameRef'], 'visualObservation': None, 'standardCategory': None}
                    for frame in images]}}
        group_key = hashlib.sha256(_raw(group)).hexdigest()[:16]
        path = target / f'images-{group_key}.json'
        _publish(path, group, directory)
        group_path = _relative(path, root)
    return group_path, cache_path, len(images), len(review.get('imagesToView', []))


def publish_navigation(directory: Path, work: dict) -> dict:
    """Publish a full ``analysis_session._work_item`` as stable CLI navigation.

    Paths are relative to ``navigation.basePath`` (the session parent). Existing
    table-encoded fact pages remain byte-for-byte unchanged. All new files and
    this response are at most 8000 UTF-8 bytes. Retries reuse immutable files;
    stashing a group changes only the content-addressed image/cache pointers.
    """
    directory = Path(os.path.abspath(directory))
    _reject_symlinks(directory, directory.parent)
    root = directory.parent
    result = {key: deepcopy(work[key]) for key in _BASIC_FIELDS if key in work}
    for key in ('sessionDir', 'analysisJson'):
        if key in result:
            result[key] = _relative(result[key], root)
    if result.get('contextHandoff', {}).get('resumePath'):
        result['contextHandoff']['resumePath'] = _relative(result['contextHandoff']['resumePath'], root)
    nav = {'basePath': str(root), 'facts': None, 'contract': None, 'references': None,
           'submissionTemplate': None, 'currentImages': None, 'cachedObservations': None}
    result.update(format='analysis-navigation-v1', navigation=nav, images=0, remainingImages=0)
    if work['status'] != 'ready_for_validation':
        task_id = work['taskId']
        if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', task_id):
            raise ValueError('invalid navigation taskId')
        target = directory / 'navigation' / task_id
        _reject_symlinks(target, directory)
        for section in ('facts', 'contract', 'references'):
            pages = work.get('referencePages' if section == 'references' else section + 'Pages')
            if pages:
                nav[section] = _page_index(directory, target / (section + '-index'), root, work, pages, section)
        template = _source(work['submissionTemplatePath'], directory)
        if template.stat().st_size > BYTE_LIMIT:
            raise NavigationCapacityError('submission template exceeds 8000 UTF-8 bytes; use --legacy-work-item and lossless reading')
        nav['submissionTemplate'] = _relative(template, root)
        if 'frameReview' in work:
            group, cached, count, remaining = _image_navigation(directory, target, root, work, work['frameReview'])
            nav.update(currentImages=group, cachedObservations=cached)
            result.update(images=count, remainingImages=remaining)
    result['readingRule'] = (work.get('readingRule', '') + '\n' +
        '所有相对路径以 navigation.basePath 为基准。facts/contract/references 是固定 entries 数组索引，'
        '沿 nextPage 读取；其事实页原有 lossless-tables-v1 编码与完整事实保持不变，按首条 readingGuide 读取。'
        '同一 taskId 暂存图片后 facts 路径不变，无需重读已读事实。只看 currentImages 本组最多3张图，'
        '直接填写 observationsTemplate 并暂存。images=0 时无需看图；从 cachedObservations 复用已存观察。'
        'submissionTemplate 只读，复制到独立提交 JSON 后填写；所有结论仍由当前模型撰写。')
    if len(_raw(result)) > BYTE_LIMIT:
        raise NavigationCapacityError('navigation response exceeds 8000 UTF-8 bytes; use a shorter session path')
    return result
