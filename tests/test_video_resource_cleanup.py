import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import video_resource_cleanup as cleanup
from report_renderer import create_report_archive, render_html_report
from analysis_handoff import file_hash, read_object


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding='utf-8')


def delivery(root):
    source = root / 'peak-frames/video-assets/m-1.mp4'
    source.parent.mkdir(parents=True, exist_ok=True)
    preview = root / 'final-report/assets/videos/m-1.mp4'
    preview.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'original video bytes')
    preview.write_bytes(source.read_bytes())
    details = {'sourceBytes': source.stat().st_size, 'sourceSha256': file_hash(source),
               'previewBytes': preview.stat().st_size, 'previewSha256': file_hash(preview),
               'deliveryStrategy': 'original-copy'}
    data = {'period': {'actualStartDate': '2026-09-01', 'actualEndDate': '2026-09-09'},
            'materials': [{'materialId': 'm-1', 'video': {'src': 'assets/videos/m-1.mp4'}}],
            'analysisQuality': {'structurally_valid': True, 'contentReviewStatus': 'reviewed_with_limits',
                                'contentReviewLimitations': [{'reason': '机器审计证据'}]}}
    write(root / 'final-report/final-report-data.json', data)
    write(root / 'final-report/presentation-report.json', {})
    (root / 'final-report/final-report.html').write_text(render_html_report(data))
    write(root / 'peak-frames/report-video-assets.json', {'materials': [{'materialId': 'm-1',
        'sourcePath': 'peak-frames/video-assets/m-1.mp4', 'previewPath': 'final-report/assets/videos/m-1.mp4', **details}]})
    write(root / 'run-summary.json', {'status': 'complete', 'artifacts': {'reportArchive': 'final-report.zip'}})
    create_report_archive(root / 'final-report')
    return source, preview


class CleanupTests(unittest.TestCase):
    def test_original_copy_cleanup_needs_no_ffmpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, preview = delivery(root)
            result = cleanup.cleanup_original_videos(
                root, ffmpeg_bin=root / 'ffmpeg-does-not-exist'
            )
            self.assertEqual(result['status'], 'planned', result)
            self.assertEqual(file_hash(source), file_hash(preview))

    def test_original_copy_archive_dry_run_delete_and_idempotent_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, preview = delivery(root)
            original_size = source.stat().st_size
            archive_hash = file_hash(root / 'final-report.zip')
            report_hash = file_hash(root / 'final-report/final-report.html')
            plan = cleanup.cleanup_original_videos(root)
            self.assertEqual(plan['status'], 'planned')
            self.assertTrue(source.is_file())
            result = cleanup.cleanup_original_videos(root, mode='delete')
            self.assertEqual(result['status'], 'complete', result)
            self.assertEqual(result['releasedBytes'], original_size)
            self.assertFalse(source.exists())
            self.assertTrue(preview.is_file())
            again = cleanup.cleanup_original_videos(root, mode='delete')
            self.assertEqual(again['releasedBytes'], original_size)
            self.assertEqual(again['status'], 'complete', again)
            self.assertEqual(file_hash(root / 'final-report.zip'), archive_hash)
            self.assertEqual(file_hash(root / 'final-report/final-report.html'), report_hash)
            self.assertEqual(read_object(root / 'run-summary.json')['status'], 'complete')

    def test_failure_gates_retain_originals(self):
        for failure in ('status', 'preview_hash', 'archive_content', 'html_source', 'source_hash', 'missing_archive', 'symlink_parent'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, preview = delivery(root)
                if failure == 'status':
                    write(root / 'run-summary.json', {'status': 'failed'})
                elif failure == 'preview_hash':
                    preview.write_bytes(b'tampered')
                elif failure == 'archive_content':
                    with zipfile.ZipFile(root / 'final-report.zip', 'w') as z:
                        z.writestr('final-report/final-report.html', 'stale')
                elif failure == 'html_source':
                    with (root / 'final-report/final-report.html').open('a') as f:
                        f.write('peak-frames/video-assets/m-1.mp4')
                elif failure == 'source_hash':
                    source.write_bytes(b'changed source')
                elif failure == 'missing_archive':
                    (root / 'final-report.zip').unlink()
                elif failure == 'symlink_parent':
                    source.parent.rename(source.parent.with_name('retained'))
                    source.parent.symlink_to(source.parent.with_name('retained'), target_is_directory=True)
                result = cleanup.cleanup_original_videos(root, mode='delete')
                self.assertEqual(result['status'], 'partial', result)
                self.assertTrue(source.exists())
                self.assertEqual(result['releasedBytes'], 0)

    def test_unlink_failure_is_partial_and_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = delivery(root)
            unlink = Path.unlink
            def fail(path, *args, **kwargs):
                if path.resolve() == source.resolve():
                    raise PermissionError('test source cannot be deleted')
                return unlink(path, *args, **kwargs)
            with patch.object(Path, 'unlink', fail):
                result = cleanup.cleanup_original_videos(root, mode='delete')
            self.assertEqual(result['status'], 'partial')
            self.assertTrue(source.exists())
            result = cleanup.cleanup_original_videos(root, mode='delete')
            self.assertEqual(result['status'], 'complete')

    def test_unmanifested_video_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = delivery(root)
            extra = source.with_name('unrelated.mp4')
            extra.write_bytes(b'retain me')
            result = cleanup.cleanup_original_videos(root, mode='delete')
            self.assertEqual(result['status'], 'complete')
            self.assertTrue(extra.exists())

    def test_cover_hides_quality_but_preserves_machine_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            delivery(root)
            html = (root / 'final-report/final-report.html').read_text()
            self.assertNotIn('结构校验：', html)
            self.assertNotIn('内容复核限制：', html)
            self.assertIn('机器审计证据', html)
            self.assertIn('统计周期', html)
            self.assertIn('assets/videos/m-1.mp4', html)
            self.assertIn('renderCombinationConclusion();', html)

    def test_failed_retry_preserves_deleted_evidence_and_dry_run_remains_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = delivery(root)
            deleted = cleanup.cleanup_original_videos(root, mode='delete')
            plan = cleanup.cleanup_original_videos(root, mode='dry-run')
            self.assertEqual(plan['status'], 'complete')
            again = cleanup.cleanup_original_videos(root, mode='delete')
            self.assertEqual(again['status'], 'complete')
            archive = root / 'final-report.zip'
            archive.write_bytes(b'corrupt')
            result = cleanup.cleanup_original_videos(root, mode='delete')
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['items'], deleted['items'])
            self.assertEqual(result['archiveSha256'], deleted['archiveSha256'])
            self.assertEqual(result['releasedBytes'], deleted['releasedBytes'])

    def test_recover_absent_source_after_durable_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = delivery(root)
            plan = cleanup.cleanup_original_videos(root)
            plan['mode'] = 'delete'
            write(root / 'final-report/video-resource-cleanup.json', plan)
            source.unlink()  # Simulated crash between unlink and receipt publication.
            recovered = cleanup.cleanup_original_videos(root, mode='delete')
            self.assertEqual(recovered['status'], 'complete')
            self.assertEqual(recovered['releasedBytes'], plan['plannedBytes'])

    def test_receipt_write_failure_after_unlink_preserves_new_deletion_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = delivery(root)
            original_writer = cleanup.write_json_atomically
            calls = 0
            def interrupted_write(path, value):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError('transient receipt failure after unlink')
                return original_writer(path, value)
            with patch.object(cleanup, 'write_json_atomically', interrupted_write):
                result = cleanup.cleanup_original_videos(root, mode='delete')
            self.assertEqual(result['status'], 'partial')
            self.assertFalse(source.exists())
            self.assertEqual(result['items'][0]['status'], 'deleted')
            self.assertGreater(result['releasedBytes'], 0)
            self.assertEqual(read_object(root / 'final-report/video-resource-cleanup.json')['items'][0]['status'], 'deleted')
