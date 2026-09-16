"""Delete only manifested source MP4s after byte-verified report delivery."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import zipfile

from batch_runtime import acquire_run_lock, write_json_atomically
from report_renderer import (
    ReportRenderingError, _sha256_file,
    _report_image_references, _report_video_references, safe_component,
)

POLICY = 'delete-original-after-validated-delivery'


def _read(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(f'JSON object required: {path}')
    return value


def _safe_file(root, relative, *, missing=False):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'unsafe resource path: {relative}')
    current = root
    for component in path.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError(f'symbolic resource path: {relative}')
    if not current.resolve().is_relative_to(root) or (not missing and not current.is_file()):
        raise ValueError(f'missing or unsafe resource path: {relative}')
    return current


def _verify_delivery(root, ffmpeg_bin, timeout_sec):
    summary = _read(_safe_file(root, 'run-summary.json'))
    if summary.get('status') != 'complete':
        raise ValueError('video cleanup requires a complete run')
    report = root / 'final-report'
    html_path = _safe_file(root, 'final-report/final-report.html')
    html = html_path.read_text(encoding='utf-8')
    images = _report_image_references(report, html)
    videos = _report_video_references(report, html)
    required = {'final-report.html', 'final-report-data.json', 'presentation-report.json', *images, *videos}
    for name in ('final-report-data.json', 'presentation-report.json'):
        _read(_safe_file(root, f'final-report/{name}'))
    if 'peak-frames/video-assets/' in html:
        raise ValueError('report still references an original video')
    archive_path = _safe_file(root, 'final-report.zip')
    if summary.get('artifacts', {}).get('reportArchive') != 'final-report.zip':
        raise ValueError('run summary archive does not match delivery')
    archive_hash = _sha256_file(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if (len(names) != len(set(names)) or archive.testzip() is not None
                or not {f'final-report/{p}' for p in required}.issubset(names)):
            raise ValueError('report archive is incomplete or corrupt')
        for name in names:
            if not name.startswith('final-report/') or '/video-assets/' in name:
                raise ValueError('unexpected archive resource')
            if name.endswith('/'):
                continue
            local = _safe_file(root, name)
            with archive.open(name) as member:
                hasher = hashlib.sha256()
                for chunk in iter(lambda: member.read(1024 * 1024), b''):
                    hasher.update(chunk)
                digest = hasher.hexdigest()
            if digest != _sha256_file(local):
                raise ValueError(f'archive resource differs from delivery: {name}')
    manifest = _read(_safe_file(root, 'peak-frames/report-video-assets.json'))
    entries = manifest.get('materials')
    if not isinstance(entries, list) or not entries:
        raise ValueError('explicit report video manifest is required')
    seen, items = set(), []
    for entry in entries:
        if not isinstance(entry.get('sourceBytes'), int) or entry['sourceBytes'] <= 0:
            raise ValueError('original byte size must be positive')
        mid = entry['materialId']
        filename = f'{safe_component(mid)}.mp4'
        source_rel = f'peak-frames/video-assets/{filename}'
        preview_rel = f'final-report/assets/videos/{filename}'
        if (entry.get('sourcePath') != source_rel or entry.get('previewPath') != preview_rel
                or preview_rel in seen or f'assets/videos/{filename}' not in videos):
            raise ValueError('video manifest path/reference mismatch')
        seen.add(preview_rel)
        source = _safe_file(root, source_rel, missing=True)
        preview = _safe_file(root, preview_rel)
        if (_sha256_file(preview) != entry.get('previewSha256')
                or preview.stat().st_size != entry.get('previewBytes')):
            raise ValueError('preview video hash/size mismatch')
        if entry.get('deliveryStrategy') == 'original-copy' and (
                entry.get('sourceSha256') != entry.get('previewSha256')
                or entry.get('sourceBytes') != entry.get('previewBytes')):
            raise ValueError('original-copy video hash/size mismatch')
        items.append({'materialId': mid, 'originalPath': source_rel,
                      'originalBytes': entry['sourceBytes'], 'originalSha256': entry['sourceSha256'],
                      'previewPath': preview_rel, 'previewSha256': entry['previewSha256'],
                      'status': 'planned'})
    if seen != {f'final-report/{v}' for v in videos}:
        raise ValueError('video manifest must cover every report video')
    if _sha256_file(archive_path) != archive_hash:
        raise ValueError("archive changed during delivery validation")
    return archive_hash, items


def cleanup_original_videos(output_dir, *, mode='dry-run', ffmpeg_bin=None, timeout_sec=600):
    """Plan or apply a cleanup, preserving a resumable receipt outside the frozen ZIP.

    A partial cleanup does not invalidate the already delivered report. Failures
    are recorded separately; retry this command, not report rendering.
    """
    if mode not in {'dry-run', 'delete', 'keep'}:
        raise ValueError('cleanup mode must be dry-run, delete or keep')
    root = Path(output_dir).resolve()
    receipt_path = root / 'final-report/video-resource-cleanup.json'
    with acquire_run_lock(root / '.video-cleanup.lock', root / 'run-summary.json'):
        # Reject symlink destinations before even writing a failure receipt.
        _safe_file(root, 'final-report/video-resource-cleanup.json', missing=True)
        previous = _read(receipt_path) if receipt_path.is_file() else {}
        result = {'schemaVersion': 1, 'policy': POLICY, 'mode': mode,
                  'status': 'planned', 'items': [], 'releasedBytes': 0,
                  'rerenderRequiresSourceRecovery': False}
        if mode == 'keep':
            if previous.get('status') in {'complete', 'partial'}:
                return previous  # Never erase evidence of earlier deletion.
            result['status'] = 'retained'
        else:
            deletion_started = False
            try:
                archive_hash, items = _verify_delivery(root, ffmpeg_bin, timeout_sec)
                result.update(archiveSha256=archive_hash, items=items)
                old_items = {i['originalPath']: i for i in previous.get('items', [])}
                for item in items:
                    source = _safe_file(root, item['originalPath'], missing=True)
                    if source.exists():
                        if (source.stat().st_size != item['originalBytes']
                                or _sha256_file(source) != item['originalSha256']):
                            raise ValueError('original video hash/size mismatch')
                    else:
                        old = old_items.get(item['originalPath'], {})
                        if (previous.get('archiveSha256') != archive_hash
                                or (old.get('status') == 'planned' and previous.get('mode') != 'delete')
                                or old.get('status') not in {'planned', 'deleted'}
                                or any(old.get(k) != item[k] for k in ('originalSha256', 'originalBytes', 'previewSha256'))):
                            raise ValueError('original is absent without matching cleanup evidence')
                        item.update(status='deleted', deletedAt=old.get('deletedAt', datetime.now(timezone.utc).isoformat()))
                if all(i['status'] == 'deleted' for i in items):
                    result['status'] = 'complete'
                result['plannedBytes'] = sum(i['originalBytes'] for i in items if i['status'] == 'planned')
                if mode == 'delete':
                    # Durable plan precedes the first unlink; a crash can resume it.
                    write_json_atomically(receipt_path, result)
                    deletion_started = True
                    for item in items:
                        if item['status'] == 'deleted':
                            continue
                        try:
                            source = _safe_file(root, item['originalPath'])
                            preview = _safe_file(root, item['previewPath'])
                            if (_sha256_file(source) != item['originalSha256']
                                    or _sha256_file(preview) != item['previewSha256']
                                    or _sha256_file(_safe_file(root, 'final-report.zip')) != archive_hash):
                                raise ValueError('resources changed after cleanup planning')
                            source.unlink()
                            if source.exists():
                                raise OSError('source still exists after unlink')
                            item.update(status='deleted', deletedAt=datetime.now(timezone.utc).isoformat())
                        except (OSError, ValueError) as exc:
                            item.update(status='failed', error=str(exc))
                        write_json_atomically(receipt_path, result)
                    result['status'] = 'complete' if all(i['status'] == 'deleted' for i in items) else 'partial'
            except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, ReportRenderingError) as exc:
                # Keep prior deletions on a failed retry; never replace their audit trail.
                if previous and not deletion_started:
                    result = previous
                result.update(status='partial', error=str(exc))
        result['releasedBytes'] = sum(i['originalBytes'] for i in result['items'] if i.get('status') == 'deleted')
        result['rerenderRequiresSourceRecovery'] = result['releasedBytes'] > 0
        write_json_atomically(receipt_path, result)
        summary_path = _safe_file(root, 'run-summary.json')
        summary = _read(summary_path)
        summary['videoResourceCleanup'] = {k: result[k] for k in ('status', 'mode', 'releasedBytes', 'rerenderRequiresSourceRecovery')}
        summary['videoResourceCleanup']['manifestPath'] = 'final-report/video-resource-cleanup.json'
        write_json_atomically(summary_path, summary)
        return result
