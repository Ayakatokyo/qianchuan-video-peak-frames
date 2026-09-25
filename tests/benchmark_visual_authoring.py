"""Measure synthetic authoring bytes; never read customer data or invoke a model."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from analysis_visuals import chapter_categories, material_observations


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def fixture():
    """119 frame references, 89 synthetic byte identities, 20 material parts."""
    descriptions = (
        ('portrait', '  合成观察：人物面部位于画面中央，背景清晰可见。\n保留原文。  '),
        ('product', '  合成观察：产品包装位于桌面中央，标签与瓶身清晰可见。\n保留原文。  '),
        ('other', '  合成观察：纯色背景上有文字信息，没有清晰人物或产品主体。\n保留原文。  '),
    )
    frames, cache, parts = [], {}, []
    for material_number in range(20):
        material_id = f'synthetic-material-{material_number + 1:03d}'
        highlights = []
        for frame_number in range(5 if material_number == 19 else 6):
            unique_number = len(frames) % 89
            image_sha = hashlib.sha256(f'synthetic-image-bytes-{unique_number}'.encode()).hexdigest()
            ref = f'highlight:{material_id}:peak_{frame_number}'
            category, description = descriptions[unique_number % 3]
            frames.append({'materialId': material_id, 'frameRef': ref, 'sha256': image_sha})
            cache.setdefault(image_sha, {'sourceFrameRef': ref, 'visualObservation': description,
                                         'standardCategory': category})
            highlights.append({'frameRef': ref, 'visualObservation': description,
                               'commercialInterpretation': '合成商业解释，仅用于字节对照，不构成实际投放判断。'})
        parts.append({'materialAnalyses': [{'materialId': material_id,
            'audienceConclusion': '合成人群正文保持不变。', 'scriptConclusion': '合成脚本正文保持不变。',
            'interactionConclusion': '合成互动正文保持不变。', 'integratedConclusion': '合成综合判断保持不变。',
            'decision': 'hold', 'nextAction': '合成下一步保持不变。', 'frameCoverageNote': '',
            'evidenceRefs': [highlights[0]['frameRef']], 'highlightAnalyses': highlights}]})
    index = {'frames': frames}
    ledger = {'inputIndexSha256': hashlib.sha256(encoded(index)).hexdigest(),
              'visualObservationCache': cache}
    standard, custom = [], []
    for category in ('portrait', 'product', 'other'):
        refs = [frame['frameRef'] for frame in frames if cache[frame['sha256']]['standardCategory'] == category]
        standard.append({'category': category, 'frameRefs': refs,
            'commonConclusion': [{'title': '合成共性', 'body': '合成共性正文保持不变。', 'evidenceRefs': refs[:1]}],
            'derivativeDirections': [{'title': '合成方向', 'body': '合成方向正文保持不变。', 'evidenceRefs': refs[:1]}]})
    for group_number in range(3):
        refs = [frame['frameRef'] for number, frame in enumerate(frames) if number % 3 == group_number]
        custom.append({'groupId': f'synthetic-{group_number}', 'groupName': f'合成机制{group_number}',
            'description': '固定合成分组，不是图像推断。', 'frameRefs': refs,
            'commonConclusion': [{'title': '合成共性', 'body': '自定义分类正文保持不变。', 'evidenceRefs': refs[:1]}],
            'derivativeDirections': [{'title': '合成方向', 'body': '自定义分类方向保持不变。', 'evidenceRefs': refs[:1]}]})
    chapter = {'highlightDashboard': {'groups': standard}, 'customClassification': {
        'ruleId': 'synthetic', 'ruleName': '合成分类', 'ruleDescription': '开发基准固定分组。',
        'ruleVersion': 'v1', 'inputFrameManifestHash': 'sha256:' + ledger['inputIndexSha256'],
        'groups': custom, 'unclassifiedFrameRefs': []}}
    return index, ledger, parts, chapter


def compare():
    index, ledger, old_parts, old_chapter = fixture()
    frozen_inputs = deepcopy((index, ledger))
    old_observations = [{key: value for key, value in entry.items() if key != 'standardCategory'}
                        for entry in ledger['visualObservationCache'].values()]
    new_observations = deepcopy(list(ledger['visualObservationCache'].values()))
    new_parts = deepcopy(old_parts)
    removed_observations = 0
    for part in new_parts:
        for material in part['materialAnalyses']:
            for frame in material['highlightAnalyses']:
                frame.pop('visualObservation')
                removed_observations += 1
    new_chapter = deepcopy(old_chapter)
    removed_refs = sum(len(group.pop('frameRefs')) for group in new_chapter['highlightDashboard']['groups'])
    frozen_submissions = deepcopy((new_parts, new_chapter))
    expanded_parts, copies = [], []
    for new_part, old_part in zip(new_parts, old_parts):
        expanded, receipt = material_observations(new_part, index, ledger)
        assert expanded == old_part, 'material expansion changed authored text or evidence'
        expanded_parts.append(expanded)
        copies.extend(receipt['observationCopies'])
    expanded_chapter, receipt = chapter_categories(new_chapter, index, ledger)
    assert expanded_chapter == old_chapter, 'classification expansion changed authored content'
    assert (index, ledger) == frozen_inputs, 'assembly mutated its source ledger'
    assert (new_parts, new_chapter) == frozen_submissions, 'assembly mutated model submissions'
    refs = [frame['frameRef'] for frame in index['frames']]
    hashes = {frame['frameRef']: frame['sha256'] for frame in index['frames']}
    expanded_refs = [frame['frameRef'] for part in expanded_parts
                     for material in part['materialAnalyses'] for frame in material['highlightAnalyses']]
    assert len(refs) == len(set(refs)) == len(expanded_refs) == 119
    assert expanded_refs == refs and len(set(hashes.values())) == 89
    for name in ('highlightDashboard', 'customClassification'):
        grouped = [ref for group in expanded_chapter[name]['groups'] for ref in group['frameRefs']]
        assert len(grouped) == 119 and set(grouped) == set(refs), 'classification lost frame coverage'
    assert len(copies) == 119 and len(receipt['standardGroupCopies']) == 3
    for copy in copies:
        entry = ledger['visualObservationCache'][copy['imageSha256']]
        assert copy['imageSha256'] == hashes[copy['frameRef']] == hashes[copy['sourceFrameRef']]
        assert copy['observationSha256'] == hashlib.sha256(entry['visualObservation'].encode()).hexdigest()
    old = {'materialParts': old_parts, 'observationStashEntries': old_observations, 'chapter04Part': old_chapter}
    new = {'materialParts': new_parts, 'observationStashEntries': new_observations, 'chapter04Part': new_chapter}
    old_bytes, new_bytes = len(encoded(old)), len(encoded(new))
    return {'schemaVersion': 1, 'fixture': {
        'source': 'synthetic in-memory fixture; not the user run or a model-generated result',
        'materialCount': 20, 'frameRefCount': 119, 'uniqueImageShaCount': 89,
        'fixtureSha256': hashlib.sha256(encoded({'index': index, 'ledger': ledger,
                                               'materialParts': old_parts, 'chapter04Part': old_chapter})).hexdigest()},
        'oldModelSubmissionUtf8Bytes': old_bytes, 'newModelSubmissionUtf8Bytes': new_bytes,
        'savedUtf8Bytes': old_bytes - new_bytes,
        'savedPercent': round(100 * (old_bytes - new_bytes) / old_bytes, 2),
        'omittedMaterialObservationFields': removed_observations,
        'omittedStandardClassificationFrameRefs': removed_refs,
        'addedStandardCategoryLabels': len(new_observations),
        'newLabelUtf8Cost': len(encoded(new_observations)) - len(encoded(old_observations)),
        'components': {name: {'oldUtf8Bytes': len(encoded(old[name])), 'newUtf8Bytes': len(encoded(new[name]))}
                       for name in old},
        'verification': {'materialPartsDeeplyEqual': True, 'chapter04PartDeeplyEqual': True,
                         'customClassificationUnchanged': True, 'frameRefsAndImageShaCoverageUnchanged': True,
                         'exactObservationTextHashVerified': True, 'sourceAndSubmissionInputsUnchanged': True},
        'measurement': 'Compact JSON UTF-8 model-authored payload bytes, with identical serialization on both sides.',
        'limits': ['119 references and 89 unique image hashes are synthetic, not measurements of the user run.',
                   'Includes authored material parts, unique-image stash entries and both classification bodies.',
                   'Excludes command envelopes, actual stash batching, tool responses, facts, images and runtime metadata.',
                   'Business prose and custom classification are unchanged; expansion uses real analysis_visuals functions.',
                   'This checks assembly equivalence, not full report content quality or model observation accuracy.',
                   'Byte savings depend on text and reference lengths; they are not tokens, latency or a speedup forecast.']}


if __name__ == '__main__':
    print(json.dumps(compare(), ensure_ascii=False, indent=2))
