"""Readable, bounded JSON task pages. No sampling or business-text generation."""
import json


def pretty_json(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def task_pages(value, budget=10000):
    def records(item, path=()):
        record = {'path': list(path), 'value': item}
        encoded = pretty_json(record)
        if len(encoded.encode()) <= budget // 2 and max(map(lambda s: len(s.encode()), encoded.splitlines())) <= 1200:
            yield record
        elif isinstance(item, dict):
            yield {'path': list(path), 'container': 'object'}
            for key, child in item.items():
                yield from records(child, (*path, key))
        elif isinstance(item, list):
            yield {'path': list(path), 'container': 'array', 'length': len(item)}
            for i, child in enumerate(item):
                yield from records(child, (*path, i))
        elif isinstance(item, str):
            yield {'path': list(path), 'container': 'string'}
            for start in range(0, len(item), 160):
                yield {'path': list(path), 'start': start, 'text': item[start:start + 160]}
        else:
            raise ValueError('task path exceeds output budget')
    pages, page = [], []
    for record in records(value):
        if len(pretty_json([record]).encode()) > budget:
            raise ValueError('task record exceeds output budget')
        if page and len(pretty_json([*page, record]).encode()) > budget:
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
