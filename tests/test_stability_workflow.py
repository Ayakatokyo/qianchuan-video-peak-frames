"""Boundary tests for production validation and post-delivery cleanup wiring."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_analysis_handoff as handoff_fixtures
import test_standard_workflow as workflow_fixtures
import standard_workflow as workflow


class StabilityWorkflowTests(unittest.TestCase):
    def test_production_validation_requires_completed_frame_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = handoff_fixtures.HandoffTests().landed_run(root)
            workflow.prepare_local_run(request, root)
            candidate = root / 'diagnostic-analysis/candidate.json'
            handoff_fixtures.write(candidate, {})
            with self.assertRaisesRegex(workflow.StandardWorkflowError, 'frame review is incomplete'):
                workflow.validate_analysis(root, candidate)

    def test_new_run_cleanup_defaults_keep_and_failures_do_not_rewind_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handoff_fixtures.write(root / 'run-summary.json', {'status': 'analysis_pending', 'request': workflow_fixtures.StandardWorkflowTests().request_payload()})
            handoff_fixtures.write(root / 'diagnostic-analysis/analysis-input-index.json', {'schemaVersion': 2})
            candidate = root / 'diagnostic-analysis/candidate.json'
            handoff_fixtures.write(candidate, {})
            with patch.object(workflow, 'cleanup_original_videos', side_effect=OSError('receipt unavailable')) as cleanup:
                result = workflow.finalize_standard_run(root, candidate,
                    render_report=lambda **kwargs: workflow_fixtures.write_rendered_report(root), mark_ready=lambda *args: True)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['videoResourceCleanup']['status'], 'partial')
            self.assertTrue((root / 'final-report.zip').is_file())
            cleanup.assert_called_once_with(root, mode='keep')
