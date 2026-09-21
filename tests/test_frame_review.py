import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import frame_review as review
from analysis_handoff import file_hash, read_object
from analysis_units_lib import publish_analysis_units
from test_analysis_units import example_analysis


def inputs(root, groups):
    facts = example_analysis()
    materials = []
    for n, images in enumerate(groups, 1):
        mid = f'm-{n}'
        frames = []
        for i, content in enumerate(images):
            path = root / f'{mid}-{i}.jpg'
            path.write_bytes(content)
            frames.append({'materialId': mid, 'rankType': f'peak_{i}', 'durationSec': i,
                           'metricValue': 1, 'framePath': str(path), 'evidenceRef': f'highlight:{mid}:peak_{i}'})
        materials.append({'materialId': mid, 'frames': frames, 'missingFrames': []})
    facts['highlights']['materials'] = materials
    facts['materials'] = [{'materialId': m['materialId']} for m in materials]
    target = root / 'diagnostic-analysis'
    publish_analysis_units(target, facts)
    (target / 'diagnostic-analysis-prompt.md').write_text('prompt')
    return target


def checkpoint(root, status):
    return review.checkpoint_frame_review(root, {'inputIndexSha256': status['inputIndexSha256'],
        'frameRefs': status['frameRefs'], 'observations': [
            {'sourceFrameRef': f['frameRef'], 'visualObservation': '测试观察：产品瓶身。'} for f in status['imagesToView']]})


class FrameReviewTests(unittest.TestCase):
    def test_dedup_across_materials_keeps_all_references_and_terminal_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'a', b'b'], [b'a', b'c']])
            s = review.start_frame_review(root)
            self.assertEqual(len(s['imagesToView']), 2)
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                review.validate_frame_review(root)
            s = checkpoint(root, s)
            self.assertEqual(s['materialId'], 'm-2')
            self.assertEqual(len(s['imagesToView']), 1)
            before = file_hash(root / 'frame-review-progress.json')
            self.assertEqual(review.start_frame_review(root), s)
            self.assertEqual(before, file_hash(root / 'frame-review-progress.json'))
            s = checkpoint(root, s)
            state = read_object(root / 'frame-review-progress.json')
            self.assertEqual((s['status'], s['completedCount'], s['pendingCount']), ('complete', 5, 0))
            self.assertEqual(state['reviewBatchCount'], 2)
            self.assertTrue(state['completedAt'])
            self.assertEqual(sum(c['viewedImageCount'] for c in state['checkpoints']), 3)
            review.validate_frame_review(root)

    def test_incomplete_batch_and_commercial_cache_never_write_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b']])
            s = review.start_frame_review(root)
            before = file_hash(root / 'frame-review-progress.json')
            for observations in ([], [{'sourceFrameRef': s['frameRefs'][0], 'visualObservation': 'a', 'commercialInterpretation': 'bad'}]):
                with self.assertRaises(ValueError):
                    review.checkpoint_frame_review(root, {'inputIndexSha256': s['inputIndexSha256'], 'frameRefs': s['frameRefs'], 'observations': observations})
                self.assertEqual(before, file_hash(root / 'frame-review-progress.json'))

    def test_changed_image_freezes_until_rebuild_and_only_changed_hash_is_viewed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b'], [b'a']])
            s = checkpoint(root, review.start_frame_review(root))
            (root.parent / 'm-1-0.jpg').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'index changed'):
                review.start_frame_review(root)
            s = review.start_frame_review(root, rebuild=True)
            self.assertEqual(len(s['imagesToView']), 1)
            self.assertTrue(list(root.glob('frame-review-progress.*.json')))
            s = checkpoint(root, s)
            self.assertEqual(len(s['imagesToView']), 1)  # Old source changed; no fabricated cache.
            checkpoint(root, s)
            review.validate_frame_review(root)

    def test_more_than_four_unique_images_splits_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[bytes([i]) for i in range(8)]])
            s = review.start_frame_review(root)
            self.assertEqual(len(s['imagesToView']), 4)
            s = checkpoint(root, s)
            self.assertEqual(len(s['imagesToView']), 4)
            self.assertEqual(checkpoint(root, s)['status'], 'complete')

    def test_legacy_six_image_batch_ledger_remains_resumable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[bytes([i]) for i in range(8)]])
            review.start_frame_review(root)
            index = read_object(root / 'analysis-input-index.json')
            state = read_object(root / 'frame-review-progress.json')
            state.pop('batchSize')
            state['batches'] = review._batches(index, 6)
            state['currentMaterialId'] = 'm-1'
            state['currentBatchFrameRefs'] = state['batches'][0]['frameRefs']
            (root / 'frame-review-progress.json').write_text(json.dumps(state))

            resumed = review.start_frame_review(root)

        self.assertEqual(len(resumed['imagesToView']), 6)

    def test_changed_prompt_or_path_blocks_existing_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a']])
            s = review.start_frame_review(root)
            (root / 'diagnostic-analysis-prompt.md').write_text('new prompt')
            with self.assertRaises(ValueError):
                checkpoint(root, s)

    def test_tampered_mapping_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a']])
            checkpoint(root, review.start_frame_review(root))
            state = read_object(root / 'frame-review-progress.json')
            state['frameRefsBySha256'] = {}
            (root / 'frame-review-progress.json').write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError, 'mapping'):
                review.validate_frame_review(root)

    def test_missing_index_can_be_explicitly_rebuilt_without_reusing_unbound_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a']])
            checkpoint(root, review.start_frame_review(root))
            (root / 'analysis-input-index.json').unlink()
            with self.assertRaises(FileNotFoundError):
                review.validate_frame_review(root)
            s = review.start_frame_review(root, rebuild=True)
            self.assertEqual(len(s['imagesToView']), 1)

    def test_changed_path_same_bytes_requires_explicit_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a']])
            s = review.start_frame_review(root)
            unit = root / 'analysis-units/chapter04-highlight-input.json'
            manifest_path = root / 'analysis-units/analysis-unit-manifest.json'
            payload = read_object(unit)
            relocated = root.parent / 'relocated.jpg'
            relocated.write_bytes(b'a')
            payload['materials'][0]['frames'][0]['framePath'] = str(relocated)
            unit.write_text(json.dumps(payload))
            manifest = read_object(manifest_path)
            for entry in manifest['units']:
                if entry['unitId'] == 'chapter04-highlight':
                    entry['sha256'], entry['byteSize'] = file_hash(unit), unit.stat().st_size
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'index changed'):
                checkpoint(root, s)
            self.assertEqual(review.start_frame_review(root, rebuild=True)['imagesToView'][0]['framePath'], str(relocated.resolve()))
