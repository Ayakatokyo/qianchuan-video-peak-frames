"""Event-driven integration checks for the real batch scheduler, without network I/O."""
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rpa_batch_runner as runner
import test_rpa_batch_runner as existing


@pytest.fixture
def batch(tmp_path, monkeypatch):
    # The existing helper supplies in-memory manifest/ready writers. Isolate the
    # scheduler's real stale-ready unlink and saved-gate lookup in a temp folder.
    monkeypatch.chdir(tmp_path)
    helper = existing.WaveBatchRunnerTests()
    helper.setUp()
    return helper


def await_release(event):
    if not event.wait(timeout=2):
        raise AssertionError('test-controlled worker was not released')


@contextmanager
def background(batch, ids, attempt_rpa, attempt_postprocess, *, release=(), **kwargs):
    result = SimpleNamespace(values=[], errors=[])

    def execute():
        try:
            result.values.append(batch.run_batch(
                ids, attempt_rpa, attempt_postprocess, poll_interval_sec=0.01, **kwargs))
        except BaseException as exc:
            result.errors.append(exc)

    thread = threading.Thread(target=execute, name='overlap-test-runner', daemon=True)
    thread.start()
    try:
        yield result
    finally:
        # Even a deliberately failing baseline assertion must release all real
        # executor workers; no test leaves a thread waiting for an absent event.
        for event in release:
            event.set()
        thread.join(timeout=2)
        assert not thread.is_alive(), 'batch did not stop after every worker was released'


def completed(result):
    assert result.errors == [], result.errors
    assert len(result.values) == 1
    return result.values[0]


@pytest.mark.parametrize('wave_barrier', [True, False])
def test_accepted_record_starts_local_work_before_later_rpa_finishes(batch, wave_barrier):
    later_rpa_started, release_later_rpa = threading.Event(), threading.Event()
    local_started = threading.Event()
    local_checks, rpa_calls = [], []

    def rpa(_args, entry):
        rpa_calls.append(entry['materialId'])
        if entry['materialId'] in {'m-3', 'm-4'}:
            later_rpa_started.set()
            await_release(release_later_rpa)
        return batch._verified_rpa_success(entry)

    def local(args, entry):
        if entry['materialId'] == 'm-1':
            persisted = any(
                item['materialId'] == 'm-1' and item.get('recordsPersisted')
                for manifest in batch.manifests for item in manifest['selectedMaterials'])
            local_checks.append((persisted, not release_later_rpa.is_set()))
            local_started.set()
        return batch._success_postprocess(args, entry)

    with background(batch, ['m-1', 'm-2', 'm-3', 'm-4'], rpa, local,
                    release=[release_later_rpa],
                    args={'target_top_n': 4, 'rpa_concurrency': 2,
                          'rpa_wave_barrier': wave_barrier}) as result:
        assert later_rpa_started.wait(timeout=1), 'later RPA wave never started'
        assert local_started.wait(timeout=1), 'accepted records waited for all later RPA'
        assert local_checks == [(True, True)], 'local work ran before persistence or after the RPA barrier'
    manifest, failures, _ = completed(result)
    assert not failures
    assert sorted(rpa_calls) == ['m-1', 'm-2', 'm-3', 'm-4']
    assert len([item for item in manifest['materials'] if item['postprocessStatus'] == 'succeeded']) == 4


@pytest.mark.parametrize('wave_barrier', [True, False])
def test_slow_local_work_does_not_delay_next_rpa_after_first_cohort_finishes(batch, wave_barrier):
    first_peer_started, release_first_peer = threading.Event(), threading.Event()
    local_started, release_local = threading.Event(), threading.Event()
    later_started = threading.Event()
    rpa_calls = []

    def rpa(_args, entry):
        mid = entry['materialId']
        rpa_calls.append(mid)
        if mid == 'm-2':
            first_peer_started.set()
            await_release(release_first_peer)
        elif mid in {'m-3', 'm-4'}:
            later_started.set()
        return batch._verified_rpa_success(entry)

    def local(args, entry):
        if entry['materialId'] == 'm-1':
            local_started.set()
            await_release(release_local)
        return batch._success_postprocess(args, entry)

    with background(batch, ['m-1', 'm-2', 'm-3', 'm-4'], rpa, local,
                    release=[release_first_peer, release_local],
                    args={'target_top_n': 4, 'rpa_concurrency': 2,
                          'rpa_wave_barrier': wave_barrier}) as result:
        assert first_peer_started.wait(timeout=1)
        assert local_started.wait(timeout=1), 'first accepted material was held behind its pending peer'
        assert not later_started.is_set(), 'first-batch CSV gate allowed an early later candidate'
        release_first_peer.set()
        assert later_started.wait(timeout=1), 'slow local work blocked the next eligible RPA wave'
        assert not release_local.is_set()
    _, failures, _ = completed(result)
    assert not failures
    assert sorted(rpa_calls) == ['m-1', 'm-2', 'm-3', 'm-4']


@pytest.mark.parametrize('wave_barrier', [True, False])
def test_unconfirmed_first_batch_blocks_later_rpa_but_keeps_local_success(batch, wave_barrier):
    rpa_calls, local_calls = [], []

    def rpa(_args, entry):
        rpa_calls.append(entry['materialId'])
        if entry['materialId'] == 'm-2':
            return {**entry, 'rpaStatus': 'rejected', 'rpaResultStatus': 'pending',
                    'remoteTaskGroupId': 'existing-remote-group', 'postprocessStatus': 'not_started'}
        return batch._verified_rpa_success(entry)

    def local(args, entry):
        local_calls.append(entry['materialId'])
        return batch._success_postprocess(args, entry)

    manifest, failures, _ = batch.run_batch(
        ['m-1', 'm-2', 'm-3'], rpa, local,
        args={'target_top_n': 2, 'rpa_concurrency': 2, 'rpa_wave_barrier': wave_barrier})
    assert sorted(rpa_calls) == ['m-1', 'm-2']
    assert local_calls == ['m-1'], 'an unconfirmed remote peer discarded accepted local work'
    assert manifest['firstBatchCsvGate']['status'] == 'unconfirmed'
    assert manifest['selectedMaterials'][0]['postprocessStatus'] == 'succeeded'
    assert failures and batch.ready_materials == []


def test_resume_processes_accepted_record_while_existing_remote_task_is_unfinished(batch):
    remote_started, release_remote, local_started = (threading.Event() for _ in range(3))
    rpa_calls, observed_videos = [], []

    def resume(_args, _candidates, entries):
        accepted = batch._verified_rpa_success(entries[0])
        accepted.update(videoPath='/tmp/retained-m-1.mp4', videoBytes=42, videoSha256='retained-image-source')
        pending = {**entries[1], 'rpaStatus': 'submitted', 'rpaResultStatus': 'pending',
                   'remoteTaskGroupId': 'saved-group-m-2'}
        entries[0], entries[1] = accepted, pending
        return {0: accepted, 1: pending}, {0: accepted}, {}, {}, [0]

    def rpa(_args, entry):
        rpa_calls.append((entry['materialId'], entry.get('remoteTaskGroupId')))
        remote_started.set()
        await_release(release_remote)
        return batch._verified_rpa_success(entry)

    def local(args, entry):
        if entry['materialId'] == 'm-1':
            observed_videos.append(entry.get('videoPath'))
            local_started.set()
        return batch._success_postprocess(args, entry)

    with background(batch, ['m-1', 'm-2'], rpa, local, release=[release_remote],
                    args={'target_top_n': 2, 'rpa_concurrency': 2}, resume_state=resume) as result:
        assert remote_started.wait(timeout=1)
        assert local_started.wait(timeout=1), 'resume held a verified record behind unfinished remote work'
        assert rpa_calls == [('m-2', 'saved-group-m-2')]
    _, failures, _ = completed(result)
    assert not failures
    assert observed_videos == ['/tmp/retained-m-1.mp4']
    assert batch.ready_materials == ['m-1', 'm-2']


def test_candidate_exhaustion_retains_local_artifacts_without_publishing_ready(batch):
    local_calls = []

    def local(args, entry):
        local_calls.append(entry['materialId'])
        return batch._success_postprocess(args, entry)

    manifest, failures, _ = batch.run_batch(
        ['m-1', 'm-2'], lambda _args, entry: batch._verified_rpa_success(entry), local,
        args={'target_top_n': 3, 'rpa_concurrency': 2})
    assert sorted(local_calls) == ['m-1', 'm-2'], 'candidate exhaustion threw away the overlap opportunity'
    assert failures and batch.ready_materials == []
    assert [(item['materialId'], item['postprocessStatus'], item.get('summaryJson'))
            for item in manifest['selectedMaterials']] == [
                ('m-1', 'succeeded', '/tmp/m-1.json'), ('m-2', 'succeeded', '/tmp/m-2.json')]
    assert manifest['rpaCollection']['acceptedCount'] == 2


def test_local_failure_cannot_backfill_before_primary_rpa_and_compensation_finish(batch):
    primary_started, release_primary = threading.Event(), threading.Event()
    two_failures_seen, compensated = threading.Event(), threading.Event()
    rpa_calls, local_attempts, backfill_checks = [], [], []

    def rpa(_args, entry):
        mid = entry['materialId']
        rpa_calls.append(mid)
        if mid == 'm-3':
            primary_started.set()
            await_release(release_primary)
        elif mid == 'm-4':
            backfill_checks.append((release_primary.is_set(), compensated.is_set()))
        return batch._verified_rpa_success(entry)

    def local(args, entry):
        if entry['materialId'] == 'm-1':
            local_attempts.append(entry['postprocessPhase'])
            if entry['postprocessPhase'] == 'serial_compensation':
                compensated.set()
            elif local_attempts.count('parallel') == 2:
                two_failures_seen.set()
            return {'result': None, 'error': RuntimeError('synthetic local failure'), 'timingsSec': {}}
        return batch._success_postprocess(args, entry)

    with background(batch, ['m-1', 'm-2', 'm-3', 'm-4'], rpa, local,
                    release=[release_primary],
                    args={'target_top_n': 3, 'rpa_concurrency': 2}) as result:
        assert primary_started.wait(timeout=1)
        assert two_failures_seen.wait(timeout=1), 'local retry work never overlapped primary RPA'
        assert 'm-4' not in rpa_calls, 'a local failure opened an extra primary RPA slot'
    manifest, failures, _ = completed(result)
    assert not failures
    assert sorted(rpa_calls) == ['m-1', 'm-2', 'm-3', 'm-4']
    assert local_attempts == ['parallel', 'parallel', 'serial_compensation']
    assert backfill_checks == [(True, True)]
    replacement = next(item for item in manifest['materials'] if item['materialId'] == 'm-4')
    assert replacement['backfillReason'] == 'postprocess_failed_after_retries'
    assert batch.ready_materials == ['m-2', 'm-3', 'm-4']


def test_postprocess_inflight_queue_is_bounded_and_interrupt_stops_unstarted_work(batch, monkeypatch):
    two_workers_started, all_rpa_started = threading.Event(), threading.Event()
    release_local, stop, interruption_saved = (threading.Event() for _ in range(3))
    local_starts, rpa_starts, postprocess_peak = [], [], []
    lock = threading.Lock()

    def rpa(_args, entry):
        with lock:
            rpa_starts.append(entry['materialId'])
            if len(rpa_starts) == 9:
                all_rpa_started.set()
        return batch._verified_rpa_success(entry)

    # Instrument the real executor, retaining its scheduling and Future behavior.
    # Counting unfinished submitted calls distinguishes a bounded queue from
    # merely bounded worker threads with an unbounded submission backlog.
    class ObservedExecutor(RealThreadPoolExecutor):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.local_futures = []

        def submit(self, function, *args, **kwargs):
            future = super().submit(function, *args, **kwargs)
            if function is not rpa:
                self.local_futures = [item for item in self.local_futures if not item.done()]
                self.local_futures.append(future)
                postprocess_peak.append(len(self.local_futures))
            return future

    monkeypatch.setattr(runner, 'ThreadPoolExecutor', ObservedExecutor)

    def local(args, entry):
        with lock:
            local_starts.append(entry['materialId'])
            if len(local_starts) == 2:
                two_workers_started.set()
        await_release(release_local)
        return batch._success_postprocess(args, entry)

    def write_json(path, payload):
        batch._write_json(path, deepcopy(payload))
        if path == 'manifest' and payload.get('interruption'):
            interruption_saved.set()

    with background(batch, [f'm-{i}' for i in range(1, 10)], rpa, local,
                    release=[release_local], args={'target_top_n': 9, 'rpa_concurrency': 3,
                    'download_concurrency': 1, 'postprocess_concurrency': 1},
                    write_json=write_json, is_interrupted=stop.is_set) as result:
        assert two_workers_started.wait(timeout=1)
        assert all_rpa_started.wait(timeout=1), 'local capacity unexpectedly blocked independent RPA collection'
        stop.set()
        assert interruption_saved.wait(timeout=1)
        assert len(local_starts) == 2
        assert max(postprocess_peak) <= 2, 'more unfinished local jobs were submitted than download + FFmpeg capacity'
    assert len(result.errors) == 1 and isinstance(result.errors[0], runner.BatchInterrupted)
    assert result.values == [] and batch.ready_materials == []
    assert len(local_starts) == 2
    assert sum(item['postprocessAttemptCount'] for item in result.errors[0].manifest['materials']) == 2


def test_interrupt_during_overlap_preserves_accepted_local_work_without_next_wave(batch):
    peer_started, release_peer = threading.Event(), threading.Event()
    local_started, release_local = threading.Event(), threading.Event()
    stop, interruption_saved = threading.Event(), threading.Event()
    rpa_starts, local_starts = [], []

    def rpa(_args, entry):
        rpa_starts.append(entry['materialId'])
        if entry['materialId'] == 'm-3':
            peer_started.set()
            await_release(release_peer)
        return batch._verified_rpa_success(entry)

    def local(args, entry):
        local_starts.append(entry['materialId'])
        local_started.set()
        await_release(release_local)
        return batch._success_postprocess(args, entry)

    def write_json(path, payload):
        batch._write_json(path, deepcopy(payload))
        if path == 'manifest' and payload.get('interruption'):
            interruption_saved.set()

    with background(batch, [f'm-{i}' for i in range(1, 7)], rpa, local,
                    release=[release_peer, release_local],
                    args={'target_top_n': 6, 'rpa_concurrency': 3,
                          'download_concurrency': 1, 'postprocess_concurrency': 1},
                    write_json=write_json, is_interrupted=stop.is_set) as result:
        assert peer_started.wait(timeout=1)
        assert local_started.wait(timeout=1), 'accepted local work did not overlap its unfinished remote peer'
        stop.set()
        assert interruption_saved.wait(timeout=1)
    assert len(result.errors) == 1 and isinstance(result.errors[0], runner.BatchInterrupted)
    assert sorted(rpa_starts) == ['m-1', 'm-2', 'm-3']
    assert 1 <= len(local_starts) <= 2
    assert batch.ready_materials == []
    retained = result.errors[0].manifest['selectedMaterials']
    assert any(item['postprocessStatus'] == 'succeeded' for item in retained)
