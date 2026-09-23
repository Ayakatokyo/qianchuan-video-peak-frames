"""Mechanical review scaffolding must never make a content decision."""
import json
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import analysis_quality as quality


def test_template_copies_all_refs_and_cannot_pass_without_model_review(tmp_path):
    candidate = tmp_path / 'candidate.json'
    candidate.write_text('{}')
    refs = [f'script:m-{i}:text:原文' for i in range(1156)]
    findings = [quality.finding('review', '核对真实限制', evidence_refs=refs,
                               required_decision='evidence_limited'),
                quality.finding('advice', '字数建议')]
    result = quality.review_result(findings, candidate, [])
    target = tmp_path / 'review.json'
    quality.write_review_template(result, target)
    template = json.loads(target.read_text())
    assert len(template['findings']) == 1
    entry = template['findings'][0]
    assert set(entry['evidenceRefs']) == set(refs)
    assert entry['decision'] is None
    assert entry['reason'] == ''
    assert entry['requiredDecision'] == 'evidence_limited'
    with pytest.raises(ValueError, match='reason'):
        quality.review_result(findings, candidate, [], quality_review=target)
    entry.update(decision='evidence_limited', reason='测试复核：原始输入缺失，保留该限制。')
    target.write_text(json.dumps(template))
    assert quality.review_result(findings, candidate, [], quality_review=target)['analysisStatus'] == 'valid'
    before = target.read_bytes()
    with pytest.raises(FileExistsError):
        quality.write_review_template(result, target)
    assert target.read_bytes() == before
    candidate.write_text('{"changed":true}')
    with pytest.raises(ValueError, match='does not match'):
        quality.review_result(findings, candidate, [], quality_review=target)


def test_template_does_not_hide_hard_errors(tmp_path):
    candidate = tmp_path / 'candidate.json'
    candidate.write_text('{}')
    findings = [quality.finding('error', 'unknown reference'),
                quality.finding('review', '复核', evidence_refs=['script:m:text:原文'])]
    result = quality.review_result(findings, candidate, [])
    with pytest.raises(ValueError, match='structurally valid'):
        quality.write_review_template(result, tmp_path / 'review.json')
    assert not (tmp_path / 'review.json').exists()


def test_validation_cli_creates_template_and_keeps_large_receipts_off_stdout(tmp_path, monkeypatch):
    import run_standard_analysis as cli
    from types import SimpleNamespace
    candidate = tmp_path / 'candidate.json'
    candidate.write_text('{}')
    result = quality.review_result([
        quality.finding('review', '复核', evidence_refs=[f'script:m-{i}:text:原文' for i in range(1156)])
    ], candidate, [])
    monkeypatch.setattr(cli, 'ensure_prepare_dependencies', lambda: None)
    monkeypatch.setattr(cli, '_load_workflow', lambda: SimpleNamespace(
        validate_analysis=lambda *args, **kwargs: result, StandardWorkflowError=RuntimeError))
    out = io.StringIO()
    target = tmp_path / 'review.json'
    args = ['validate-analysis', '--output-dir', str(tmp_path), '--analysis-json', str(candidate)]
    with redirect_stdout(out):
        code = cli.main([*args, '--review-template', str(target)])
    assert code == 3
    assert 'quality_review_template=' in out.getvalue()
    assert len(json.loads(target.read_text())['findings'][0]['evidenceRefs']) == 1156
    assert 'script:m-' not in out.getvalue()
    assert len(out.getvalue().encode()) < 1500
    result.update(analysisStatus='valid', reviewDecisions=json.loads(target.read_text())['findings'],
                  contentReviewLimitations=json.loads(target.read_text())['findings'], pendingReviewIds=[])
    out = io.StringIO()
    with redirect_stdout(out):
        assert cli.main(args) == 0
    assert 'script:m-' not in out.getvalue()
