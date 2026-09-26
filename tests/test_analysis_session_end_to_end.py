"""Exercise the real CLI through every authoring slot with synthetic offline facts."""
import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_standard_analysis as cli
from analysis_handoff import file_hash, inspect_inputs, read_object, verify_assembly
from analysis_task_io import restore_task_pages
from analysis_units_lib import publish_analysis_units
from test_analysis_units import example_analysis


OBSERVATIONS = {
    'highlight:m-1:peak_0': '红色纯色填满画面，没有人物、文字与产品。',
    'highlight:m-1:peak_1': '红色纯色填满画面，没有人物、文字与产品。',
    'highlight:m-1:peak_2': '蓝色纯色填满画面，没有人物、文字与产品。',
    'highlight:m-1:peak_3': '浅灰色纯色填满画面，没有人物、文字与产品。',
    'highlight:m-1:peak_4': '浅灰色纯色填满画面，没有人物、文字与产品。',
    'highlight:m-1:peak_5': '白色纯色填满画面，没有人物、文字与产品。',
}


def synthetic_inputs(run):
    facts = example_analysis()
    audience = facts['audience']['metrics']['SHOW_COUNT']
    for metric in ('CLICK_COUNT', 'TRANSACTION_AMOUNT'):
        facts['audience']['metrics'][metric] = copy.deepcopy(audience)
    frames = []
    for number, color in enumerate(('red', 'red', 'blue', 'gray', 'gray', 'white')):
        path = run / f'frame-{number}.jpg'
        Image.new('RGB', (16, 16), color).save(path, format='JPEG')
        frames.append({'materialId': 'm-1', 'rankType': f'peak_{number}', 'durationSec': number,
                       'metricValue': 1, 'framePath': str(path),
                       'evidenceRef': f'highlight:m-1:peak_{number}'})
    facts['highlights']['materials'][0].update(frames=frames, frameCoverage={'succeededFrameCount': 6})
    root = run / 'diagnostic-analysis'
    publish_analysis_units(root, facts)
    (root / 'diagnostic-analysis-prompt.md').write_text('Offline synthetic current-model authoring contract.', encoding='utf-8')
    return root


def pages(entries):
    payloads = []
    for entry in entries:
        path = Path(entry['path'])
        assert path.stat().st_size == entry['byteSize'] <= 8000
        assert file_hash(path) == entry['sha256']
        payloads.append(read_object(path)['records'])
    return restore_task_pages(payloads)


def advance(run, flag=None, payload=None):
    args = ['analysis-session', '--output-dir', str(run)]
    if flag:
        request = run / 'diagnostic-analysis' / 'current-request.json'
        request.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        args.extend([flag, str(request)])
    output = io.StringIO()
    with redirect_stdout(output):
        code = cli.main(args)
    assert code == 0, output.getvalue()
    assert len(output.getvalue().encode()) <= 8000
    work = json.loads(output.getvalue())
    return pages(work['workItemPages']) if 'workItemPages' in work else work


def submit(run, work, part):
    return advance(run, '--submission-json', {'inputSha256': work['inputSha256'],
        'taskId': work['taskId'], 'part': part})


def authored_parts(manifest_sha):
    refs = list(OBSERVATIONS)
    chapter01 = {'analysisProvenance': {'author': 'current_model', 'businessTextGeneration': 'direct'},
        'overallConclusion': '  保留这段模型原文、标点与换行。\n样本仅支持本批次的有限判断。  ',
        'portfolioDimensionConclusions': {'commercial': {
            'conclusion': '单素材成本事实不足以推断规模化收益。', 'evidenceRefs': []}}}
    chapter02 = {'batchInsights': [
        {'topic': 'audience', 'title': '展现样本边界', 'conclusion': '展现人群样本均来自同一素材。',
         'evidenceRefs': ['audience:SHOW_COUNT:user_group_label_name:Z世代']},
        {'topic': 'audience', 'title': '点击样本边界', 'conclusion': '点击人群不能代替成交人群解释。',
         'evidenceRefs': ['audience:CLICK_COUNT:user_group_label_name:Z世代']},
        {'topic': 'audience', 'title': '成交样本边界', 'conclusion': '当前成交分布缺少其他素材对照。',
         'evidenceRefs': ['audience:TRANSACTION_AMOUNT:user_group_label_name:Z世代']}]}
    chapter03 = {'batchInsights': [
        {'topic': 'script', 'title': '标签结构', 'conclusion': '现有标签只明确了适用场景。',
         'evidenceRefs': ['script:m-1:商品卖点:适用场景']},
        {'topic': 'script', 'title': '脚本证据边界', 'conclusion': '早餐口播不能支持额外功效推论。',
         'evidenceRefs': ['script:m-1:商品卖点:适用场景']}],
        'keywordCloudReview': [], 'keywordCloudInsight': {
            'analysisStatus': 'insufficient_evidence', 'limitation': '合成样本只有一条场景标签，无法提炼三个独立卖点。',
            'oneSentenceConclusion': '场景标签只支持早餐使用情境。',
            'summary': '只保留已有场景证据，卖点丰富度需要更多真实脚本文本。', 'sellingPoints': []}}
    material = {'materialAnalyses': [{'materialId': 'm-1',
        'audienceConclusion': '三类指标来自同一条合成素材。',
        'scriptConclusion': '现有脚本仅支持场景识别。',
        'interactionConclusion': '少量点击不能归因于背景颜色。',
        'integratedConclusion': '六帧仅包含四种纯色画面；重复画面保留引用，当前证据不支持商业归因。',
        'decision': 'hold', 'nextAction': '补充真实产品与人物画面对照后再评估。',
        'frameCoverageNote': '', 'evidenceRefs': refs,
        'highlightAnalyses': [{'frameRef': ref, 'visualObservation': observation,
                              'interpretationStatus': 'insufficient_evidence',
                              'limitation': '纯色合成图没有可以验证商业作用的主体或信息。'}
                             for ref, observation in OBSERVATIONS.items()]}]}
    common = {'title': '纯色样本', 'body': '所有画面只有背景色，没有可验证的产品与人物信息。', 'evidenceRefs': refs}
    direction = {'title': '保持背景对照', 'body': '固定其他变量后增加真实产品主体，再比较背景色。', 'evidenceRefs': refs}
    groups = []
    for group_id, name, indices in [('red', '红色背景', [0, 1]), ('blue', '蓝色背景', [2]), ('neutral', '灰白背景', [3, 4, 5])]:
        group_refs = [refs[index] for index in indices]
        groups.append({'groupId': group_id, 'groupName': name, 'description': name + '的合成对照组。',
                       'frameRefs': group_refs,
                       'commonConclusion': [{**common, 'evidenceRefs': group_refs}],
                       'derivativeDirections': [{**direction, 'evidenceRefs': group_refs}]})
    chapter04 = {'highlightDashboard': {'groups': [{'category': 'other', 'frameRefs': refs,
        'commonConclusion': [common], 'derivativeDirections': [direction]}]},
        'customClassification': {'ruleId': 'synthetic-background', 'ruleName': '背景颜色对照',
            'ruleDescription': '仅按合成图可见背景色划分，不推断商业效果。', 'ruleVersion': 'v1',
            'inputFrameManifestHash': 'sha256:' + manifest_sha,
            'groups': groups, 'unclassifiedFrameRefs': []}}
    return [('chapter01', chapter01), ('chapter02', chapter02), ('chapter03', chapter03),
            ('material-001', material), ('chapter04', chapter04)]


def test_cli_complete_session_preserves_prose_observations_and_assembly_sources(tmp_path):
    root = synthetic_inputs(tmp_path)
    index = inspect_inputs(root)
    authored = authored_parts(index['manifestSha256'])
    work = advance(tmp_path)
    assert work['taskCount'] == 5
    for task_id, part in authored[:3]:
        assert work['taskId'] == task_id
        assert pages(work['factsPages'])['taskId'] == task_id
        work = submit(tmp_path, work, part)

    assert work['taskId'] == 'material-001'
    initial_review = work['frameReview']
    assert len(initial_review['frameRefs']) == 6
    assert len(initial_review['imagesToView']) == 4
    assert [len(group['imagesToView']) for group in initial_review['visionBatches']] == [3, 1]
    first_images = initial_review['imagesToView'][:2]
    batch = copy.deepcopy(initial_review['checkpointTemplate'])
    batch['observations'] = [{'sourceFrameRef': image['frameRef'],
                             'visualObservation': OBSERVATIONS[image['frameRef']]} for image in first_images]
    partial = advance(tmp_path, '--observations-json', batch)
    ledger_path = root / 'frame-review-progress.json'
    partial_ledger = ledger_path.read_bytes()
    assert partial['frameReview']['completedCount'] == 0
    assert partial['frameReview']['pendingCount'] == 6
    assert partial['frameReview']['reviewBatchCount'] == 0
    assert len(partial['frameReview']['stashedObservations']) == 2
    assert advance(tmp_path, '--observations-json', batch) == partial
    assert ledger_path.read_bytes() == partial_ledger
    resumed = advance(tmp_path)
    assert resumed == partial
    remaining = resumed['frameReview']['imagesToView']
    assert [image['frameRef'] for image in remaining] == ['highlight:m-1:peak_3', 'highlight:m-1:peak_5']
    seen_hashes = [image['sha256'] for image in first_images + remaining]
    assert len(seen_hashes) == len(set(seen_hashes)) == index['uniqueImageCount'] == 4
    remaining_batch = copy.deepcopy(resumed['frameReview']['checkpointTemplate'])
    remaining_batch['observations'] = [{'sourceFrameRef': image['frameRef'],
        'visualObservation': OBSERVATIONS[image['frameRef']]} for image in remaining]
    reviewed = advance(tmp_path, '--observations-json', remaining_batch)
    assert reviewed['frameReview']['imagesToView'] == []
    assert reviewed['completedTaskCount'] == 3  # Observations do not author the material's prose.
    assert advance(tmp_path, '--observations-json', remaining_batch) == reviewed
    ledger = read_object(ledger_path)
    assert ledger['status'] == 'complete'
    assert ledger['reviewBatchCount'] == 1
    assert ledger['checkpoints'][0]['viewedImageCount'] == 4
    assert ledger['checkpoints'][0]['cacheHitCount'] == 2

    work = submit(tmp_path, reviewed, authored[3][1])
    assert work['taskId'] == 'chapter04'
    visuals = pages(work['factsPages'])['reviewedVisuals']
    assert visuals['frameCount'] == 6
    assert len(visuals['images']) == 4
    assert {item['sha256'] for item in visuals['images']} == set(seen_hashes)
    assert {ref for item in visuals['images'] for ref in item['frameRefs']} == set(OBSERVATIONS)
    for item in visuals['images']:
        assert item['visualObservation'] == OBSERVATIONS[item['sourceFrameRef']]
        assert item['frameRefs'] == [frame['frameRef'] for frame in index['frames'] if frame['sha256'] == item['sha256']]
        assert all(OBSERVATIONS[ref] == item['visualObservation'] for ref in item['frameRefs'])

    done = submit(tmp_path, work, authored[4][1])
    assert done['status'] == 'ready_for_validation'
    assert done['taskId'] is None
    assert done['completedTaskCount'] == 5
    assert done['contentReviewed'] is False
    assert done['finalValidationRequired'] is True
    candidate_path = Path(done['analysisJson'])
    candidate = read_object(candidate_path)
    expected = {'schemaVersion': 5, **authored[0][1], **authored[2][1], **authored[3][1], **authored[4][1],
                'batchInsights': authored[1][1]['batchInsights'] + authored[2][1]['batchInsights']}
    assert candidate == expected  # Preserve every supplied string, newline, field and list order.
    state = read_object(root / 'analysis-session/state.json')
    receipt = read_object(candidate_path.with_suffix('.assembly.json'))
    assert [item['taskId'] for item in state['completed']] == [task_id for task_id, _ in authored]
    assert receipt['candidateSha256'] == file_hash(candidate_path)
    assert receipt['manifestSha256'] == index['manifestSha256']
    assert receipt['promptSha256'] == index['promptSha256']
    assert len(receipt['sources']) == 5
    for source, saved, (_, part) in zip(receipt['sources'], state['completed'], authored):
        path = Path(source['path'])
        assert path == root / 'analysis-session' / saved['partPath']
        assert source['sha256'] == saved['partSha256'] == file_hash(path)
        assert read_object(path) == part
    assert verify_assembly(candidate_path, index) == file_hash(candidate_path.with_suffix('.assembly.json'))
    before = candidate_path.read_bytes(), candidate_path.with_suffix('.assembly.json').read_bytes()
    assert advance(tmp_path) == done
    assert (candidate_path.read_bytes(), candidate_path.with_suffix('.assembly.json').read_bytes()) == before
