"""Model-authored visual categories remain bound to their exact observations."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import frame_review as review
from analysis_handoff import read_object
from test_frame_review import inputs


def observation_batch(status, entries):
    return {'inputIndexSha256': status['inputIndexSha256'],
            'frameRefs': status['frameRefs'], 'observations': entries}


class FrameReviewCategoryTests(unittest.TestCase):
    def test_category_survives_stash_restart_exact_retry_and_same_image_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a'], [b'a']])
            status = review.start_frame_review(root)
            entry = {'sourceFrameRef': 'highlight:m-1:peak_0',
                     'visualObservation': '白色瓶身位于桌面中央。',
                     'standardCategory': 'product'}
            batch = observation_batch(status, [entry])
            review.stash_frame_observations(root, batch)
            before = (root / 'frame-review-progress.json').read_bytes()
            resumed = review.start_frame_review(root)
            self.assertEqual(list(resumed['cachedObservations'].values()), [entry])
            self.assertEqual(resumed['imagesToView'], [])
            self.assertEqual(review.stash_frame_observations(root, copy.deepcopy(batch)), resumed)
            self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)
            second = review.checkpoint_frame_review(root, batch)
            self.assertEqual(second['imagesToView'], [])
            self.assertEqual(list(second['cachedObservations'].values()), [entry])
            done = review.checkpoint_frame_review(root, second['checkpointTemplate'])
            self.assertEqual((done['status'], done['completedCount']), ('complete', 2))
            review.validate_frame_review(root)

    def test_mixed_categories_and_legacy_observations_complete_without_inferring_category(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b', b'c', b'd']])
            status = review.start_frame_review(root)
            entries = [
                {'sourceFrameRef': 'highlight:m-1:peak_0', 'visualObservation': '人物面部占据画面。',
                 'standardCategory': 'portrait'},
                {'sourceFrameRef': 'highlight:m-1:peak_1', 'visualObservation': '产品瓶身特写。',
                 'standardCategory': 'product'},
                {'sourceFrameRef': 'highlight:m-1:peak_2', 'visualObservation': '纯色背景上的文字。',
                 'standardCategory': 'other'},
                {'sourceFrameRef': 'highlight:m-1:peak_3', 'visualObservation': '人物手持白色产品瓶。'},
            ]
            review.checkpoint_frame_review(root, observation_batch(status, entries))
            saved = read_object(root / 'frame-review-progress.json')['visualObservationCache']
            self.assertEqual(list(saved.values()), entries)
            self.assertEqual(review.start_frame_review(root)['status'], 'complete')
            review.validate_frame_review(root)

    def test_legacy_templates_and_legacy_submissions_remain_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a']])
            status = review.start_frame_review(root)
            group_template = status['visionBatches'][0]['stashTemplate']
            self.assertEqual(group_template['observations'], [
                {'sourceFrameRef': 'highlight:m-1:peak_0', 'visualObservation': None}])
            self.assertEqual(status['checkpointTemplate']['observations'], group_template['observations'])
            group_template['observations'][0]['visualObservation'] = '桌面上摆放产品。'
            review.stash_frame_observations(root, group_template)
            resumed = review.start_frame_review(root)
            self.assertEqual(list(resumed['cachedObservations'].values()), [
                {'sourceFrameRef': 'highlight:m-1:peak_0', 'visualObservation': '桌面上摆放产品。'}])
            self.assertEqual(review.checkpoint_frame_review(root, resumed['checkpointTemplate'])['status'], 'complete')

    def test_invalid_category_or_extra_field_never_partially_writes_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b']])
            status = review.start_frame_review(root)
            first = {'sourceFrameRef': 'highlight:m-1:peak_0', 'visualObservation': '瓶身特写。',
                     'standardCategory': 'product'}
            second = {'sourceFrameRef': 'highlight:m-1:peak_1', 'visualObservation': '人物特写。'}
            invalid = [{**second, 'standardCategory': value}
                       for value in (None, '', 'unknown', 'PORTRAIT', False, 1, [], {})]
            invalid.append({**second, 'standardCategory': 'portrait', 'commercialInterpretation': '不可缓存'})
            before = (root / 'frame-review-progress.json').read_bytes()
            for entry in invalid:
                with self.subTest(entry=entry):
                    with self.assertRaises(ValueError):
                        review.stash_frame_observations(root, observation_batch(status, [first, entry]))
                    self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)

    def test_existing_category_cannot_be_changed_removed_or_added_by_retry(self):
        for original_category, proposed_category in [('product', 'portrait'), ('product', None), (None, 'product')]:
            with self.subTest(original=original_category, proposed=proposed_category):
                with tempfile.TemporaryDirectory() as directory:
                    root = inputs(Path(directory), [[b'a']])
                    status = review.start_frame_review(root)
                    entry = {'sourceFrameRef': 'highlight:m-1:peak_0', 'visualObservation': '瓶身特写。'}
                    if original_category is not None:
                        entry['standardCategory'] = original_category
                    review.stash_frame_observations(root, observation_batch(status, [entry]))
                    before = (root / 'frame-review-progress.json').read_bytes()
                    changed = {k: v for k, v in entry.items() if k != 'standardCategory'}
                    if proposed_category is not None:
                        changed['standardCategory'] = proposed_category
                    with self.assertRaisesRegex(ValueError, 'conflict'):
                        review.stash_frame_observations(root, observation_batch(status, [changed]))
                    self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)

    def test_invalid_category_in_saved_ledger_is_rejected_on_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a']])
            status = review.start_frame_review(root)
            review.stash_frame_observations(root, observation_batch(status, [
                {'sourceFrameRef': 'highlight:m-1:peak_0', 'visualObservation': '瓶身特写。',
                 'standardCategory': 'product'}]))
            original = read_object(root / 'frame-review-progress.json')
            for value in (None, 'unknown', [], {}):
                with self.subTest(category=value):
                    corrupted = copy.deepcopy(original)
                    next(iter(corrupted['visualObservationCache'].values()))['standardCategory'] = value
                    path = root / 'frame-review-progress.json'
                    path.write_text(json.dumps(corrupted))
                    before = path.read_bytes()
                    with self.assertRaises(ValueError):
                        review.start_frame_review(root)
                    self.assertEqual(path.read_bytes(), before)

    def test_rebuild_preserves_category_only_for_unchanged_image_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b']])
            status = review.start_frame_review(root)
            entries = [
                {'sourceFrameRef': 'highlight:m-1:peak_0', 'visualObservation': '人物面部特写。',
                 'standardCategory': 'portrait'},
                {'sourceFrameRef': 'highlight:m-1:peak_1', 'visualObservation': '白色瓶身特写。',
                 'standardCategory': 'product'},
            ]
            review.stash_frame_observations(root, observation_batch(status, entries))
            (root.parent / 'm-1-0.jpg').write_bytes(b'changed image')
            rebuilt = review.start_frame_review(root, rebuild=True)
            self.assertEqual([frame['frameRef'] for frame in rebuilt['imagesToView']], ['highlight:m-1:peak_0'])
            self.assertEqual(list(rebuilt['cachedObservations'].values()), [entries[1]])

    def test_category_does_not_relax_current_source_or_same_sha_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'a'], [b'future']])
            status = review.start_frame_review(root)
            first = {'sourceFrameRef': 'highlight:m-1:peak_0', 'visualObservation': '白色瓶身特写。',
                     'standardCategory': 'product'}
            before = (root / 'frame-review-progress.json').read_bytes()
            invalid = [
                [{**first, 'sourceFrameRef': 'highlight:m-2:peak_0'}],
                [first, {**first, 'sourceFrameRef': 'highlight:m-1:peak_1', 'standardCategory': 'portrait'}],
            ]
            for entries in invalid:
                with self.subTest(entries=entries):
                    with self.assertRaises(ValueError):
                        review.stash_frame_observations(root, observation_batch(status, entries))
                    self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)
