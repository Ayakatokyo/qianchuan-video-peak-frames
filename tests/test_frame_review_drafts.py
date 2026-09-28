"""Persist model-authored observations without completing an unfinished batch."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import frame_review as review
from analysis_handoff import inspect_inputs, read_object
from test_frame_review import inputs, checkpoint


def payload(status, images=None):
    return {'inputIndexSha256': status['inputIndexSha256'],
            'frameRefs': list(status['frameRefs']),
            'observations': [{'sourceFrameRef': frame['frameRef'],
                              'visualObservation': '瓶身位于画面中央，白色标签可见。'}
                             for frame in (status['imagesToView'] if images is None else images)]}


class FrameReviewDraftTests(unittest.TestCase):
    def stash(self, root, batch, **kwargs):
        method = getattr(review, 'stash_frame_observations', None)
        self.assertTrue(callable(method), 'partial observations need a durable stash API')
        return method(root, batch, **kwargs)

    def test_vision_groups_limit_unique_images_without_changing_six_frame_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'a', b'b', b'c', b'd', b'e', b'f']])
            status = review.start_frame_review(root)
            self.assertIn('visionBatches', status)
            groups = status['visionBatches']
            self.assertEqual([len(group['imagesToView']) for group in groups], [6])
            self.assertEqual(len(status['frameRefs']), 7)
            self.assertEqual([frame['frameRef'] for group in groups for frame in group['imagesToView']],
                             ['highlight:m-1:peak_0', 'highlight:m-1:peak_2', 'highlight:m-1:peak_3',
                              'highlight:m-1:peak_4', 'highlight:m-1:peak_5', 'highlight:m-1:peak_6'])
            for group in groups:
                self.assertEqual(group['stashTemplate']['frameRefs'], status['frameRefs'])
                self.assertEqual(group['stashTemplate']['inputIndexSha256'], status['inputIndexSha256'])
            self.assertEqual(checkpoint(root, status)['reviewBatchCount'], 1)

    def test_two_of_six_stashed_observations_survive_restart_without_completing_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[bytes([i]) for i in range(6)]])
            first = review.start_frame_review(root)
            self.stash(root, payload(first, first['imagesToView'][:2]))
            resumed = review.start_frame_review(root)
            state = read_object(root / 'frame-review-progress.json')
            self.assertEqual((resumed['completedCount'], resumed['pendingCount'], resumed['reviewBatchCount']), (0, 6, 0))
            self.assertEqual(len(resumed['imagesToView']), 4)
            self.assertEqual(len(resumed['cachedObservations']), 2)
            self.assertEqual(len(resumed['stashedObservations']), 2)
            self.assertEqual(state['completedFrameRefs'], [])
            self.assertEqual(state['frameRefsBySha256'], {})
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                review.validate_frame_review(root)
            self.stash(root, payload(resumed))
            done = review.checkpoint_frame_review(root, payload(first, []))
            self.assertEqual((done['status'], done['completedCount'], done['reviewBatchCount']), ('complete', 6, 1))
            self.assertEqual(read_object(root / 'frame-review-progress.json')['checkpoints'][0]['viewedImageCount'], 6)
            review.validate_frame_review(root)

    def test_identical_stash_is_byte_idempotent_and_conflicting_text_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b']])
            status = review.start_frame_review(root)
            batch = payload(status, status['imagesToView'][:1])
            first = self.stash(root, batch)
            before = (root / 'frame-review-progress.json').read_bytes()
            self.assertEqual(self.stash(root, copy.deepcopy(batch)), first)
            self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)
            batch['observations'][0]['visualObservation'] = '冲突的新描述'
            with self.assertRaisesRegex(ValueError, 'conflict'):
                self.stash(root, batch)
            self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)

    def test_duplicate_entries_are_idempotent_but_conflicts_within_one_payload_do_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b']])
            status = review.start_frame_review(root)
            batch = payload(status, status['imagesToView'][:1])
            batch['observations'] *= 2
            stashed = self.stash(root, batch)
            self.assertEqual(len(stashed['cachedObservations']), 1)
            batch = payload(stashed)
            batch['observations'].append({**batch['observations'][0], 'visualObservation': '不同观察'})
            before = (root / 'frame-review-progress.json').read_bytes()
            with self.assertRaisesRegex(ValueError, 'conflict'):
                self.stash(root, batch)
            self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)

    def test_full_checkpoint_can_include_the_same_previously_stashed_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b']])
            status = review.start_frame_review(root)
            self.stash(root, payload(status, status['imagesToView'][:1]))
            result = review.checkpoint_frame_review(root, payload(status))
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(read_object(root / 'frame-review-progress.json')['checkpoints'][0]['viewedImageCount'], 2)

    def test_invalid_payloads_never_change_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b'], [b'future']])
            status = review.start_frame_review(root)
            good = payload(status, status['imagesToView'][:1])
            invalid = [None, [], {}, {**good, 'commercialInterpretation': 'bad'},
                       {**good, 'inputIndexSha256': 'old'},
                       {**good, 'frameRefs': status['frameRefs'][:1]},
                       {**good, 'frameRefs': list(reversed(status['frameRefs']))},
                       {**good, 'observations': {}}, {**good, 'observations': [None]}]
            for value in [None, '', ' \n ', 1, {}, []]:
                invalid.append({**good, 'observations': [
                    {'sourceFrameRef': status['frameRefs'][0], 'visualObservation': value}]})
            for ref in ['highlight:m-2:peak_0', 'unknown', [], None]:
                invalid.append({**good, 'observations': [
                    {'sourceFrameRef': ref, 'visualObservation': '真实观察'}]})
            invalid.append({**good, 'observations': [
                {**good['observations'][0], 'commercialInterpretation': 'bad'}]})
            before = (root / 'frame-review-progress.json').read_bytes()
            for batch in invalid:
                with self.subTest(batch=batch):
                    with self.assertRaises(ValueError):
                        self.stash(root, batch)
                    self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)

    def test_stale_batch_and_changed_inputs_are_rejected_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a'], [b'b']])
            first = review.start_frame_review(root)
            second = checkpoint(root, first)
            before = (root / 'frame-review-progress.json').read_bytes()
            with self.assertRaises(ValueError):
                self.stash(root, payload(first))
            self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)
            (root.parent / 'm-2-0.jpg').write_bytes(b'new input')
            with self.assertRaisesRegex(ValueError, 'index changed'):
                self.stash(root, payload(second))
            self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)

    def test_previous_observation_reuses_actual_sha_but_future_source_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a'], [b'a'], [b'c']])
            first = review.start_frame_review(root)
            batch = payload(first)
            batch['observations'][0]['sourceFrameRef'] = 'highlight:m-2:peak_0'
            with self.assertRaises(ValueError):
                self.stash(root, batch)
            second = checkpoint(root, first)
            self.assertEqual(second['imagesToView'], [])
            self.assertEqual(second['visionBatches'], [])
            self.assertEqual(second['checkpointTemplate']['observations'], [])
            third = review.checkpoint_frame_review(root, second['checkpointTemplate'])
            self.assertEqual((third['materialId'], len(third['imagesToView'])), ('m-3', 1))

    def test_legacy_four_image_batch_remains_frozen_during_stash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[bytes([i]) for i in range(6)]])
            review.start_frame_review(root)
            state = read_object(root / 'frame-review-progress.json')
            state['batchSize'] = 4
            refs = list(state['pendingFrameRefs'])
            state['batches'] = [{'materialId': 'm-1', 'frameRefs': refs[:4]},
                                {'materialId': 'm-1', 'frameRefs': refs[4:]}]
            state['currentBatchFrameRefs'] = refs[:4]
            (root / 'frame-review-progress.json').write_text(json.dumps(state))
            status = review.start_frame_review(root)
            self.stash(root, payload(status))
            status = review.checkpoint_frame_review(root, payload(status, []))
            self.assertEqual(status['frameRefs'], refs[4:])
            self.assertEqual(checkpoint(root, status)['reviewBatchCount'], 2)

    def test_shared_context_performs_transitions_against_one_verified_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b']])
            units = {}
            context = inspect_inputs(root, loaded_units=units), units
            with patch.object(review, 'inspect_inputs', side_effect=AssertionError('duplicate input scan')):
                status = review.start_frame_review(root, _context=context)
                self.stash(root, payload(status), _context=context)
                _, state = review._load(root, _context=context)
                self.assertEqual(state['completedFrameRefs'], [])
                done = review.checkpoint_frame_review(root, payload(status, []), _context=context)
            self.assertEqual(done['status'], 'complete')

    def test_prepare_transition_is_read_only_and_requires_complete_coverage_when_requested(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b']])
            status = review.start_frame_review(root)
            index, state = review._load(root)
            original = copy.deepcopy(state)
            before = (root / 'frame-review-progress.json').read_bytes()
            method = getattr(review, '_prepare_frame_observations', None)
            self.assertTrue(callable(method), 'session needs a no-write observation transition')
            draft = method(index, state, payload(status, status['imagesToView'][:1]))
            self.assertEqual(len(draft['visualObservationCache']), 1)
            self.assertEqual(state, original)
            with self.assertRaisesRegex(ValueError, 'unreviewed'):
                method(index, state, payload(status, status['imagesToView'][:1]), complete=True)
            self.assertEqual((root / 'frame-review-progress.json').read_bytes(), before)

    def test_rebuild_reuses_unchanged_stashes_and_reviews_changed_jpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = inputs(Path(directory), [[b'a', b'b', b'c']])
            first = review.start_frame_review(root)
            self.stash(root, payload(first, first['imagesToView'][:2]))
            (root.parent / 'm-1-0.jpg').write_bytes(b'new a')
            rebuilt = review.start_frame_review(root, rebuild=True)
            self.assertEqual([f['frameRef'] for f in rebuilt['imagesToView']],
                             ['highlight:m-1:peak_0', 'highlight:m-1:peak_2'])
            self.assertEqual(len(rebuilt['cachedObservations']), 1)
            self.assertEqual(rebuilt['reviewBatchCount'], 0)
