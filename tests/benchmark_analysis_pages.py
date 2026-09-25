"""Offline, lossless page-size comparison. Reads facts; never invokes a model."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import analysis_tasks as tasks
from analysis_task_io import pretty_json, restore_task_pages
from analysis_reader import json_text
from final_report_lib import ALLOWED_DECISIONS


def compare(path, baseline_ref):
    baseline = types.ModuleType('baseline_io')
    exec(subprocess.check_output(['git', 'show', baseline_ref + ':scripts/analysis_task_io.py'],
                                 cwd=REPO, text=True), baseline.__dict__)
    rows = []
    for index, material in enumerate(json.loads(path.read_text())['materials']):
        task_id = f'material-{index + 1:03d}'
        packet = {'taskId': task_id, 'sourceFile': 'chapter05-material-input.json',
            'sourcePath': ['materials', index], 'writingRule': tasks.WRITING_RULE,
            'scopedFacts': tasks.model_view(task_id, material), 'allowedDecisions': sorted(ALLOWED_DECISIONS),
            'frameWritingRule': ('每帧写 visualObservation 与有证据的 commercialInterpretation；'
                '不足时用 interpretationStatus=insufficient_evidence + limitation，不能强编。'
                '同素材同图可用 shared + sharedInterpretationRef 指向直接 supported 帧；观察缓存不替代商业分析。')}
        envelope = {'inputSha256': 'a' * 64, 'taskId': task_id, 'section': 'facts',
            'page': 9999999999, 'pageCount': 9999999999, 'nextPage': 9999999999}
        old = baseline.task_pages(packet, budget=8000, envelope=envelope)
        new = tasks._pages(packet, 'a' * 64, task_id, 'facts')
        assert json_text(restore_task_pages(new)) == json_text(packet)
        def total(pages, render):
            count = 0
            for number, records in enumerate(pages):
                raw = render({**envelope, 'page': number, 'pageCount': len(pages),
                    'nextPage': number + 1 if number + 1 < len(pages) else None, 'records': records}) + '\n'
                assert len(raw.encode()) <= 8000
                assert max(len(line.encode()) for line in raw.splitlines()) <= 1200
                count += len(raw.encode())
            return count
        rows.append({'task': task_id, 'oldPages': len(old), 'newPages': len(new),
            'oldBytes': total(old, baseline.pretty_json), 'newBytes': total(new, pretty_json)})
    return {'fixtureSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'baselineRef': baseline_ref, 'rows': rows,
        'total': {key: sum(row[key] for row in rows) for key in ('oldPages', 'newPages', 'oldBytes', 'newBytes')},
        'losslessRoundtrip': True, 'measurement': 'UTF-8 file bytes and page counts; not model latency or tokens'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--material-facts-json', type=Path, required=True)
    parser.add_argument('--baseline-ref', default='96a0f29ac5fed010212d348124af49047720f9cb')
    args = parser.parse_args()
    print(json.dumps(compare(args.material_facts_json, args.baseline_ref), ensure_ascii=False, indent=2))
