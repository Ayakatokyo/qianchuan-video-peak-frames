"""Bounded, readable task exports retain every scoped fact and exact type."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import analysis_task_io as task_io
import analysis_tasks as tasks
import test_analysis_reader as fixtures


def material_packet():
    metrics = {}
    refs = []
    for metric in ('SHOW_COUNT', 'CLICK_COUNT', 'TRANSACTION_AMOUNT'):
        dimensions = {}
        for dimension in ('user_group_label_name', 'age', 'gender', 'province'):
            rows = []
            for number in range(8):
                ref = f'audience:{metric}:{dimension}:人群分类{number}'
                refs.append(ref)
                rows.append({'bucket': f'人群分类{number}', 'metricValue': number * 173.25,
                             'metricPercentage': number * 2.5,
                             'evidenceRef': ref, 'available': True})
            dimensions[dimension] = {'dimensionKey': dimension, 'totalMetricValue': 54321.0,
                                     'buckets': rows}
        metrics[metric] = {'available': True, 'dimensions': dimensions}
    return {'taskId': 'material-001', 'sourcePath': ['materials', 0],
            'writingRule': tasks.WRITING_RULE,
            'scopedFacts': {'materialId': 'm-1', 'script': {'text': '完整脚本。' * 80},
                            'audience': {'metrics': metrics},
                            'evidenceIndex': {'audience': refs},
                            'typed': [None, False, 0, 0.0, '0', [], {}, {'$table': 1}]}}


def test_compact_task_pages_reduce_round_trips_and_restore_all_fact_types():
    packet = material_packet()
    pages = tasks._pages(packet, 'a' * 64, 'material-001', 'facts')
    assert len(pages) <= 3
    assert pages[0][0]['encoding'] == 'lossless-tables-v1'
    restored = task_io.restore_task_pages(pages)
    assert json.dumps(restored, ensure_ascii=False) == json.dumps(packet, ensure_ascii=False)
    for number, page in enumerate(pages):
        payload = {'inputSha256': 'a' * 64, 'taskId': 'material-001', 'section': 'facts',
                   'page': number, 'pageCount': len(pages),
                   'nextPage': number + 1 if number + 1 < len(pages) else None,
                   'records': page}
        raw = task_io.pretty_json(payload)
        assert len((raw + '\n').encode()) <= 8000
        assert max(len(line.encode()) for line in raw.splitlines()) <= 1200


def test_compact_json_retains_rows_on_readable_bounded_lines():
    value = {'rows': [{'name': '人群', 'amount': i, 'empty': None} for i in range(8)]}
    encoded = task_io.pretty_json(value)
    assert len(encoded.encode()) < len(json.dumps(value, ensure_ascii=False, indent=1).encode()) * .8
    assert json.loads(encoded) == value
    with pytest.raises(ValueError):
        task_io.pretty_json({'bad': float('nan')})


def test_read_and_export_share_encoded_pages_and_bind_loaded_units(tmp_path):
    root = fixtures.AnalysisReaderTests().inputs(tmp_path)
    units = {}
    inputs, binding, packets = tasks._packets(root, section='facts', loaded_units=units)
    assert len(units) == 5
    assert inputs['materialIds'] == ['m-1']
    result = tasks.export_tasks(root, root / 'compact-export', section='facts')
    index = json.loads(Path(result['indexPath']).read_text())
    for task in index['tasks']:
        pages = []
        for number, entry in enumerate(task['pages']):
            raw = (Path(result['indexPath']).parent / entry['path']).read_bytes()
            assert len(raw) == entry['byteSize'] <= 8000
            assert hashlib.sha256(raw).hexdigest() == entry['sha256']
            exported = json.loads(raw)
            assert exported == tasks.read_task(root, task['taskId'], number, binding, section='facts')
            pages.append(exported['records'])
        packet = next(packet for item, _, packet in packets if item['taskId'] == task['taskId'])
        assert task_io.restore_task_pages(pages) == packet


def test_chapter04_contract_exposes_exact_category_enum_and_order(tmp_path):
    root = fixtures.AnalysisReaderTests().inputs(tmp_path)
    _, _, packets = tasks._packets(root, task_id='chapter04', section='contract')
    contract = packets[0][2]
    assert contract['allowedCategories'] == ['portrait', 'product', 'other']
    assert 'portrait' in contract['fieldRules']['categoryOrder']
    assert 'other' in contract['fieldRules']['categoryOrder']
