"""Readable, bounded JSON task pages. No sampling or business-text generation."""
import json

TABLE_ENCODING = 'lossless-tables-v1'
TABLE_READING_GUIDE = (
    'Reconstruct path/value and container/start/items/text records first. '
    '$table.columns lists nested field paths in column order; common maps zero-based '
    'column indexes to unchanged values. Each row supplies only non-common columns. '
    'kind=array preserves row order; kind=object pairs keys with rows. '
    '$literal preserves an original reserved-key object without decoding it. '
    'No facts, types, order or evidence references are omitted.')


def pretty_json(value):
    """Keep top-level fields and long containers readable, compact short rows.

    This changes whitespace only. It never shortens strings or drops values.
    Task pagination splits oversized scalar strings before final rendering.
    """
    def compact(item):
        return json.dumps(item, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

    def render(item, depth=0, prefix_size=0, *, inline=True):
        raw = compact(item)
        if inline and len(raw.encode()) + depth + prefix_size <= 1100:
            return raw
        padding, child_padding = ' ' * depth, ' ' * (depth + 1)
        if isinstance(item, dict) and item:
            lines = []
            for key, child in item.items():
                # json.dumps owns JSON's key coercion rules (bool/None/numbers).
                key_text = compact({key: None})[1:-5]
                lines.append(child_padding + key_text + render(
                    child, depth + 1, len(key_text.encode())))
            return '{\n' + ',\n'.join(lines) + '\n' + padding + '}'
        if isinstance(item, (list, tuple)) and item:
            return '[\n' + ',\n'.join(child_padding + render(child, depth + 1)
                                       for child in item) + '\n' + padding + ']'
        return raw

    return render(value, inline=False)


def task_pages(value, budget=10000, *, envelope=None, encoding=None):
    if encoding not in (None, TABLE_ENCODING):
        raise ValueError('unsupported task encoding')
    if encoding:
        from analysis_reader import encode_tables
        value = encode_tables(value)

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
    pages = []
    page = ([{'path': [], 'encoding': encoding, 'readingGuide': TABLE_READING_GUIDE}]
            if encoding else [])
    if page_size(page) > budget:
        raise ValueError('task encoding guide exceeds output budget')
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
    root, encoding = None, None
    for page in pages:
        for record in page:
            if 'encoding' in record:
                if root is not None or encoding is not None or record['encoding'] != TABLE_ENCODING:
                    raise ValueError('invalid task encoding header')
                encoding = record['encoding']
                continue
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
    if encoding:
        from analysis_reader import decode_tables
        return decode_tables(root)
    return root
