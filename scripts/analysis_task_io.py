"""Readable, bounded JSON task pages. No sampling or business-text generation."""
import json


def pretty_json(value):
    # One-space indentation keeps structural cues without paying for deep
    # duplicated whitespace in every tool response.
    return json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False)


def task_pages(value, budget=10000, *, envelope=None):
    def page_size(page):
        payload = {**envelope, 'records': page} if envelope is not None else page
        return len((pretty_json(payload) + '\n').encode())

    def fits_record(record):
        encoded = pretty_json(record)
        return (len(encoded.encode()) <= budget // 2
                and max(len(line.encode()) for line in encoded.splitlines()) <= 1200)

    def records(item, path=()):
        record = {'path': list(path), 'value': item}
        if fits_record(record):
            yield record
        elif isinstance(item, dict):
            yield {'path': list(path), 'container': 'object'}
            for key, child in item.items():
                yield from records(child, (*path, key))
        elif isinstance(item, list):
            yield {'path': list(path), 'container': 'array', 'length': len(item)}
            pending, start = [], 0
            for i, child in enumerate(item):
                if pending and not fits_record({'path': list(path), 'start': start, 'items': [*pending, child]}):
                    yield {'path': list(path), 'start': start, 'items': pending}
                    pending = []
                if not fits_record({'path': list(path), 'start': i, 'items': [child]}):
                    yield from records(child, (*path, i))
                else:
                    if not pending:
                        start = i
                    pending.append(child)
            if pending:
                yield {'path': list(path), 'start': start, 'items': pending}
        elif isinstance(item, str):
            yield {'path': list(path), 'container': 'string'}
            for start in range(0, len(item), 160):
                yield {'path': list(path), 'start': start, 'text': item[start:start + 160]}
        else:
            raise ValueError('task path exceeds output budget')
    pages, page = [], []
    for record in records(value):
        if page_size([record]) > budget:
            raise ValueError('task record exceeds output budget')
        if page and page_size([*page, record]) > budget:
            pages.append(page)
            page = []
        page.append(record)
    if page:
        pages.append(page)
    return pages


def restore_task_pages(pages):
    """Lossless reconstruction for verification and offline consumers."""
    root = None
    for page in pages:
        for record in page:
            path = record['path']
            if 'value' in record:
                value = record['value']
            elif 'container' in record:
                value = {'object': {}, 'array': [None] * record.get('length', 0), 'string': ''}[record['container']]
            else:
                value = root
                for key in path:
                    value = value[key]
                if 'items' in record:
                    start = record['start']
                    value[start:start + len(record['items'])] = record['items']
                    continue
                if len(value) != record['start']:
                    raise ValueError('non-contiguous text')
                value += record['text']
            if not path:
                root = value
            else:
                parent = root
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = value
    return root
