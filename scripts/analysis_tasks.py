"""Default concise model tasks; full chapter facts remain the report authority."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path
import tempfile
import time

from analysis_reader import _snapshot, _tasks, json_text
import analysis_task_io
import analysis_units_lib
from analysis_task_io import task_pages
from analysis_units_lib import script_label_evidence_refs
from batch_runtime import write_json_atomically

TASK_SECTIONS = ('all', 'facts', 'contract', 'references')

WRITING_RULE = ('统一精简分析：总览100–150字；维度40–70字；脚本每条50–80字；'
                '唯一画面观察通常40–70字；素材核心判断80–120字；每组一条共性和一个测试方向。'
                '字数仅为上限倾向，证据不足可更短，不凑字；null必须由模型填写，不能直接交付。')


def evidence_refs(value):
    if isinstance(value, dict):
        return set().union(*(evidence_refs(x) for x in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(evidence_refs(x) for x in value)) if value else set()
    return {value} if isinstance(value, str) and value.startswith(
        ('commercial:', 'audience:', 'script:', 'highlight:', 'interaction:')) else set()


def model_view(task_id, source):
    result = deepcopy(source)
    if task_id == 'chapter01':
        portfolio = result['portfolio']
        raw = portfolio.pop('materials', {})
        rows = list(raw.values()) if isinstance(raw, dict) else raw
        costs, missing = [], 0
        for row in rows:
            try:
                cost = Decimal(str(row.get('totalCost')))
                if not cost.is_finite() or cost < 0:
                    raise InvalidOperation
                costs.append((cost, row))
            except (InvalidOperation, ValueError):
                missing += 1
        costs.sort(key=lambda pair: -pair[0])
        total = sum((cost for cost, _ in costs), Decimal(0))
        top = sum((cost for cost, _ in costs[:10]), Decimal(0))
        portfolio['costDistribution'] = {
            'sourceCount': len(rows), 'missingCostCount': missing,
            'totalCost': str(total) if not missing else None,
            'top10Cost': str(top) if not missing else None,
            'top10Share': str(top / total) if total and not missing else None,
            'zeroCostCount': sum(cost == 0 for cost, _ in costs),
            'sourcePath': 'portfolio.materials', 'scope': 'all_portfolio_materials',
            'ratioUnit': 'fraction_0_1',
        }
        portfolio['topCostMaterials'] = [row for _, row in costs[:10]]
        result['readingBoundary'] = ('全部榜单用于确定性汇总；topCostMaterials仅按消耗降序取前10，'
                                     '不能代表长尾。原始明细仍在章节事实包；其他聚合、爆款事实保持原值。')
    if task_id == 'chapter02' or task_id.startswith('material-'):
        metrics = result.get('metrics', {}) if task_id == 'chapter02' else result.get('audience', {}).get('metrics', {})
        displayed_refs = set()
        for metric, metric_value in metrics.items():
            for dimension, value in metric_value.get('dimensions', {}).items():
                buckets = value.get('buckets', [])
                if len(buckets) > 8:
                    value['bucketReadingCoverage'] = {'sourceCount': len(buckets), 'includedCount': 5,
                        'omittedCount': len(buckets) - 5, 'selection': 'original_order_first_5'}
                    value['buckets'] = buckets[:5]
                for bucket in value.get('buckets', []):
                    displayed_refs.add(f"audience:{metric}:{dimension}:{bucket.get('bucket', '')}")
        if 'audience' in result.get('evidenceIndex', {}):
            result['evidenceIndex']['audience'] = [ref for ref in result['evidenceIndex']['audience'] if ref in displayed_refs]
        result['audienceReadingBoundary'] = ('全部维度与原分母保留；超过8个桶的维度只读原顺序前5桶，'
            'bucketReadingCoverage记录省略量。未展示桶仍在原包，不用当前桶重新计算总体比例。')
    if task_id.startswith('material-'):
        for metric in result.get('interactionTimeline', {}).get('metrics', []):
            if 'points' in metric:
                metric['pointCount'] = len(metric.pop('points'))
        result['readingBoundary'] = ('秒级原始points只供报告曲线展示；本任务保留峰值、可用性与引用。'
                                     '不得据此推断未给出的区间走势或画面因果；需时序细节时定向读取原包。')
    return result


def _insight(topic, title):
    return {'topic': topic, 'title': title, 'conclusion': None, 'evidenceRefs': []}


def output_skeleton(task_id, facts, manifest_sha):
    point = {'title': None, 'body': None, 'evidenceRefs': []}
    if task_id == 'chapter01':
        return {'analysisProvenance': {'author': 'current_model', 'businessTextGeneration': 'direct'},
                'overallConclusion': None, 'portfolioDimensionConclusions': {
                    'commercial': {'conclusion': None, 'evidenceRefs': []}},
                'hotMaterialInsight': {'conclusion': None, 'evidenceRefs': []}}
    if task_id == 'chapter02':
        return {'batchInsights': [_insight('audience', name) for name in ('展现人群', '点击人群', '成交人群')]}
    if task_id == 'chapter03':
        return {'batchInsights': [_insight('script', name) for name in ('一二级内容公式', '脚本文案')],
                'keywordCloudReview': [], 'keywordCloudInsight': {'analysisStatus': None,
                'limitation': '', 'oneSentenceConclusion': None, 'summary': None,
                'sellingPoints': [{'dimension': None, 'title': None, 'description': None,
                                   'keywordRefs': [], 'evidenceRefs': []} for _ in range(3)]}}
    if task_id == 'chapter04':
        group = {'frameRefs': [], 'commonConclusion': [deepcopy(point)], 'derivativeDirections': [deepcopy(point)]}
        return {'highlightDashboard': {'groups': [{**deepcopy(group), 'category': None}]},
                'customClassification': {'ruleId': None, 'ruleName': None, 'ruleDescription': None,
                  'ruleVersion': 'v1', 'inputFrameManifestHash': 'sha256:' + manifest_sha,
                  'groups': [{**deepcopy(group), 'groupId': None, 'groupName': None, 'description': None}],
                  'unclassifiedFrameRefs': []}}
    return {'materialAnalyses': [{'materialId': facts['materialId'], **{field: None for field in (
        'audienceConclusion', 'scriptConclusion', 'interactionConclusion', 'integratedConclusion', 'decision', 'nextAction')},
        'frameCoverageNote': None if facts.get('highlight', {}).get('missingFrames') else '', 'evidenceRefs': [],
        'highlightAnalyses': [{'frameRef': f['evidenceRef'], 'visualObservation': None,
                              'interpretationStatus': 'observation_only'} for f in facts.get('highlight', {}).get('frames', [])]}]}


def _packets(root, task_id=None, section='all', *, loaded_units=None):
    if section not in TASK_SECTIONS:
        raise ValueError('unknown analysis task section')
    inputs, binding, units = _snapshot(root)
    if loaded_units is not None:
        loaded_units.update(units)
    implementation = (Path(__file__).read_bytes() + Path(analysis_task_io.__file__).read_bytes()
                      + Path(analysis_units_lib.__file__).read_bytes())
    binding = hashlib.sha256((binding + hashlib.sha256(implementation).hexdigest()).encode()).hexdigest()
    packets = []
    for task in _tasks(units, inputs['materialIds']):
        if task_id is not None and task['taskId'] != task_id:
            continue
        source = units[task['unitId']]
        for key in task['sourcePath']:
            source = source[key]
        facts = model_view(task['taskId'], source)
        refs = evidence_refs(facts)
        if task['taskId'] == 'chapter01':
            from final_report_lib import _chapter01_evidence_refs
            refs |= _chapter01_evidence_refs(source)
        packet = {'taskId': task['taskId'], 'sourceFile': task['sourceFile'], 'sourcePath': task['sourcePath'],
                  'writingRule': WRITING_RULE, 'outputSkeleton': output_skeleton(task['taskId'], facts, inputs['manifestSha256']),
                  'allowedEvidenceRefs': sorted(refs), 'scopedFacts': facts}
        if task['taskId'] == 'chapter02':
            packet['requiredAudienceMetrics'] = ['SHOW_COUNT', 'CLICK_COUNT', 'TRANSACTION_AMOUNT']
        if task['taskId'] == 'chapter03':
            packet['evidenceRules'] = {
                'batchInsights': {'rule': 'Use script label references, not keyword text references.',
                                  'allowedEvidenceRefs': sorted(script_label_evidence_refs(source['materials']))},
                'keywordCloudInsight.sellingPoints': {
                    'rule': 'keywordRefs are exact keywordCloud.keywords[*].keyword strings, not script refs or paraphrases. Each keywordRef must bind to its own candidate evidenceRefs; text references belong here.',
                    'sourcePath': 'keywordCloud.keywords[*].evidenceRefs'},
            }
            if section == 'facts':
                packet['evidenceRules']['batchInsights'].pop('allowedEvidenceRefs')
        if task['taskId'] == 'chapter04':
            packet['allowedCategories'] = ['portrait', 'product', 'other']
            packet['fieldRules'] = {'customClassification': 'top-level, alongside highlightDashboard',
                'frameRefs': 'Each successful frame exactly once per classification; group insight refs stay within its frameRefs',
                'categoryOrder': 'highlightDashboard.groups uses only nonempty categories in portrait, product, other order; custom names belong only to customClassification.groups'}
        if task['taskId'].startswith('material-'):
            from final_report_lib import ALLOWED_DECISIONS
            packet['allowedDecisions'] = sorted(ALLOWED_DECISIONS)
            packet['frameWritingRule'] = ('每帧只写一段 visualObservation，通常 40–70 字，完整交代主体、动作/状态、产品和关键字幕；'
                '按实际可见内容取舍，不凑字，不展开无关服饰、陈设和光效。'
                'interpretationStatus 固定为 observation_only，不写 commercialInterpretation、limitation 或 sharedInterpretationRef；'
                '不可辨认的信息在描述中简短说明。相同 JPEG 直接复用观察，商业判断集中在素材核心结论和第四章。')
        if section != 'all':
            selected = {'facts': 'scopedFacts', 'contract': 'outputSkeleton',
                        'references': 'allowedEvidenceRefs'}[section]
            for key in ('scopedFacts', 'outputSkeleton', 'allowedEvidenceRefs'):
                if key != selected:
                    packet.pop(key)
        packets.append((task, source, packet))
    return inputs, binding, packets


def _pages(packet, binding, task_id, section):
    # Reserve the actual CLI envelope, including indentation and newline. Large
    # cursors bound the smaller real page numbers without iterative pagination.
    envelope = {'inputSha256': binding, 'taskId': task_id, 'section': section,
                'page': 9999999999, 'pageCount': 9999999999, 'nextPage': 9999999999}
    return task_pages(packet, budget=8000, envelope=envelope, encoding=analysis_task_io.TABLE_ENCODING)


def task_plan(root, *, section='all'):
    start = time.perf_counter()
    inputs, binding, packets = _packets(root, section=section)
    tasks = [{**task, 'sourceBytes': len(json_text(source).encode()),
              'taskBytes': len(json_text(packet).encode()),
              'pageCount': len(_pages(packet, binding, task['taskId'], section))}
             for task, source, packet in packets]
    return {'format': 'concise-tasks-v1', 'inputSha256': binding, 'manifestSha256': inputs['manifestSha256'],
            'recordsEncoding': analysis_task_io.TABLE_ENCODING,
            'readingGuide': analysis_task_io.TABLE_READING_GUIDE,
            'section': section, 'pageByteLimit': 8000,
            'materialIds': inputs['materialIds'], 'frameCount': len(inputs['frames']),
            'uniqueImageCount': inputs['uniqueImageCount'], 'contentReviewed': False, 'tasks': tasks,
            'measurement': 'UTF-8 bytes and local preparation seconds; not model tokens or model elapsed time',
            'prepareSeconds': round(time.perf_counter() - start, 6)}


def read_task(root, task_id, page, input_sha256, *, section='all'):
    _, binding, packets = _packets(root, task_id=task_id, section=section)
    if binding != input_sha256:
        raise ValueError('analysis inputs changed; get a new task plan')
    packet = next((packet for task, _, packet in packets if task['taskId'] == task_id), None)
    if packet is None:
        raise ValueError('unknown analysis task')
    pages = _pages(packet, binding, task_id, section)
    if type(page) is not int or not 0 <= page < len(pages):
        raise ValueError('analysis page is out of range')
    return {'inputSha256': binding, 'taskId': task_id, 'section': section, 'page': page, 'pageCount': len(pages),
            'nextPage': page + 1 if page + 1 < len(pages) else None, 'records': pages[page]}


def export_tasks(root, export_dir, *, task_id=None, input_sha256=None, section='facts'):
    """Verify once and publish bounded pages; no generated prose or review state."""
    root, destination = Path(root).resolve(), Path(export_dir).absolute()
    if destination.exists() or destination.is_symlink() or not destination.resolve().is_relative_to(root):
        raise ValueError('use a new task export directory within diagnostic-analysis')
    inputs, binding, packets = _packets(root, task_id=task_id, section=section)
    if input_sha256 is not None and input_sha256 != binding:
        raise ValueError('analysis inputs changed; get a new task plan')
    if not packets:
        raise ValueError('unknown analysis task')
    index = {'format': 'concise-task-export-v1', 'inputSha256': binding,
             'recordsEncoding': analysis_task_io.TABLE_ENCODING,
             'readingGuide': analysis_task_io.TABLE_READING_GUIDE,
             'manifestSha256': inputs['manifestSha256'], 'promptSha256': inputs['promptSha256'],
             'section': section, 'pageByteLimit': 8000, 'contentReviewed': False, 'tasks': []}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.task-export-', dir=destination.parent) as staging:
        staging = Path(staging)
        for task, _, packet in packets:
            pages = _pages(packet, binding, task['taskId'], section)
            entry = {**task, 'pages': []}
            for number, records in enumerate(pages):
                payload = {'inputSha256': binding, 'taskId': task['taskId'], 'section': section,
                           'page': number, 'pageCount': len(pages),
                           'nextPage': number + 1 if number + 1 < len(pages) else None, 'records': records}
                raw = (analysis_task_io.pretty_json(payload) + '\n').encode()
                name = f"{task['taskId']}.{number:03d}.json"
                (staging / name).write_bytes(raw)
                entry['pages'].append({'path': name, 'byteSize': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
            index['tasks'].append(entry)
        write_json_atomically(staging / 'index.json', index)
        staging.rename(destination)
    return {'indexPath': str(destination / 'index.json'), 'inputSha256': binding,
            'taskCount': len(index['tasks']), 'pageCount': sum(len(t['pages']) for t in index['tasks']),
            'section': section, 'contentReviewed': False}
