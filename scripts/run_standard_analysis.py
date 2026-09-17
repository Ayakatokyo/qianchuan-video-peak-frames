#!/usr/bin/env python3
"""CLI adapter for the standard Qianchuan analysis workflow."""

import argparse
import json
from pathlib import Path
import sys

from dependency_bootstrap import DependencyBootstrapError, ensure_prepare_dependencies


def _load_workflow():
    """Import workflow modules only after the dependency gate has passed."""
    import standard_workflow

    return standard_workflow


def _positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("top-n must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("top-n must be a positive integer")
    return number


def build_parser():
    parser = argparse.ArgumentParser(
        description="Run the standard Qianchuan analysis workflow."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser("prepare")
    prepare.add_argument("--start-date", required=True)
    prepare.add_argument("--end-date", required=True)
    prepare.add_argument("--top-n", required=True, type=_positive_int)
    prepare.add_argument("--advertiser-id", required=True)
    prepare.add_argument("--shop-id", required=True)
    prepare.add_argument("--output-dir", required=True)

    resume = commands.add_parser("resume", help="Resume an interrupted standard run")
    resume.add_argument("--start-date", required=True)
    resume.add_argument("--end-date", required=True)
    resume.add_argument("--top-n", required=True, type=_positive_int)
    resume.add_argument("--advertiser-id", required=True)
    resume.add_argument("--shop-id", required=True)
    resume.add_argument("--output-dir", required=True)

    local = commands.add_parser("prepare-local", parents=[], help="Build missing facts from landed data only")
    local.add_argument("--start-date", required=True)
    local.add_argument("--end-date", required=True)
    local.add_argument("--top-n", required=True, type=_positive_int)
    local.add_argument("--advertiser-id", required=True)
    local.add_argument("--shop-id", required=True)
    local.add_argument("--output-dir", required=True)

    assemble = commands.add_parser("assemble-analysis")
    assemble.add_argument("--output-dir", required=True)
    assemble.add_argument("--part", action="append", required=True)
    assemble.add_argument("--analysis-json", required=True)

    plan = commands.add_parser("analysis-plan", help="Inspect lossless model input tasks without changing facts")
    plan.add_argument("--output-dir", required=True)
    plan.add_argument("--verify", action="store_true", help="Verify exact round-trip equality for every task")

    read = commands.add_parser("read-analysis", help="Read one bounded page of frozen model facts")
    read.add_argument("--output-dir", required=True)
    read.add_argument("--task", required=True)
    read.add_argument("--page", type=int, default=0)
    read.add_argument("--input-sha256", required=True)

    check_part = commands.add_parser("check-analysis-part", help="Check a model-authored material part before assembly")
    check_part.add_argument("--output-dir", required=True)
    check_part.add_argument("--part", required=True)

    review = commands.add_parser("frame-review", help="Freeze, resume or checkpoint actual JPEG observations")
    review.add_argument("--output-dir", required=True)
    review.add_argument("--batch-json")
    review.add_argument("--rebuild", action="store_true")

    cleanup = commands.add_parser("cleanup-videos", help="Verify delivery and plan/apply original MP4 cleanup")
    cleanup.add_argument("--output-dir", required=True)
    cleanup.add_argument("--mode", choices=("dry-run", "delete", "keep"), default="dry-run")

    metadata = commands.add_parser("fetch-metadata")
    metadata.add_argument("--start-date", required=True)
    metadata.add_argument("--end-date", required=True)
    metadata.add_argument("--advertiser-id", required=True)
    metadata.add_argument("--shop-id", required=True)
    metadata.add_argument("--output-dir", required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--output-dir", required=True)
    finalize.add_argument("--analysis-json", required=True)
    finalize.add_argument("--quality-review")
    finalize.add_argument("--video-cleanup", choices=("dry-run", "delete", "keep"))

    validate_analysis = commands.add_parser("validate-analysis")
    validate_analysis.add_argument("--output-dir", required=True)
    validate_analysis.add_argument("--analysis-json", required=True)
    validate_analysis.add_argument("--quality-review")

    status = commands.add_parser("status")
    status.add_argument("--output-dir", required=True)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code
    workflow = None
    try:
        if args.command == "check-analysis-part":
            from analysis_handoff import check_material_part
            print(json.dumps(check_material_part(Path(args.output_dir) / "diagnostic-analysis", args.part), ensure_ascii=False))
            return 0
        if args.command in {"analysis-plan", "read-analysis"}:
            from analysis_reader import analysis_plan, read_analysis, json_text
            root = Path(args.output_dir) / "diagnostic-analysis"
            result = (analysis_plan(root, verify=args.verify) if args.command == "analysis-plan"
                      else read_analysis(root, args.task, args.page, args.input_sha256))
            print(json_text(result))
            return 0
        if args.command == "frame-review":
            from frame_review import start_frame_review, checkpoint_frame_review
            from analysis_handoff import read_object
            root = Path(args.output_dir) / "diagnostic-analysis"
            if args.batch_json and args.rebuild:
                raise ValueError("rebuild and batch checkpoint must be separate commands")
            result = (checkpoint_frame_review(root, read_object(args.batch_json)) if args.batch_json
                      else start_frame_review(root, rebuild=args.rebuild))
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "cleanup-videos":
            from video_resource_cleanup import cleanup_original_videos
            result = cleanup_original_videos(args.output_dir, mode=args.mode)
            print(json.dumps(result, ensure_ascii=False))
            return 2 if result['status'] == 'partial' else 0
        if args.command in {"prepare-local", "assemble-analysis"}:
            ensure_prepare_dependencies(allow_install=False)
        else:
            ensure_prepare_dependencies()
        workflow = _load_workflow()
        if args.command in {"prepare", "prepare-local", "resume"}:
            if args.command == "prepare":
                prepare_run = workflow.prepare_standard_run
            elif args.command == "prepare-local":
                prepare_run = workflow.prepare_local_run
            else:
                prepare_run = workflow.resume_standard_run
            summary = prepare_run(
                workflow.WorkflowRequest(
                    start_date=args.start_date,
                    end_date=args.end_date,
                    top_n=args.top_n,
                    advertiser_id=args.advertiser_id,
                    shop_id=args.shop_id,
                ),
                args.output_dir,
            )
            artifacts = summary["artifacts"]
            print(f"run_summary={Path(args.output_dir) / 'run-summary.json'}")
            print(f"diagnostic_analysis_prompt={Path(args.output_dir) / artifacts['diagnosticPrompt']}")
            print(f"analysis_unit_manifest={Path(args.output_dir) / artifacts['analysisUnitManifest']}")
        elif args.command == "fetch-metadata":
            workflow.fetch_report_metadata(
                workflow.WorkflowRequest(
                    start_date=args.start_date,
                    end_date=args.end_date,
                    top_n=None,
                    advertiser_id=args.advertiser_id,
                    shop_id=args.shop_id,
                ),
                args.output_dir,
            )
            print(f"run_summary={Path(args.output_dir) / 'run-summary.json'}")
            print(f"gateway_response={Path(args.output_dir) / 'source-data' / 'gateway-response.json'}")
        elif args.command == "finalize":
            options = {"quality_review": args.quality_review} if args.quality_review else {}
            if args.video_cleanup:
                options["video_cleanup"] = args.video_cleanup
            summary = workflow.finalize_standard_run(args.output_dir, args.analysis_json, **options)
            artifacts = summary["artifacts"]
            report_dir = Path(args.output_dir) / "final-report"
            print(f"run_summary={Path(args.output_dir) / 'run-summary.json'}")
            print(f"final_report_data={report_dir / 'final-report-data.json'}")
            print(f"final_report_html={Path(args.output_dir) / artifacts['report']}")
            if artifacts.get("highlightExport"):
                print(f"highlight_export={Path(args.output_dir) / artifacts['highlightExport']}")
            print(f"final_report_zip={Path(args.output_dir) / artifacts['reportArchive']}")
            if summary.get("videoResourceCleanup"):
                print(f"video_cleanup_status={summary['videoResourceCleanup']['status']}")
        elif args.command == "validate-analysis":
            options = {"quality_review": args.quality_review} if args.quality_review else {}
            result = workflow.validate_analysis(args.output_dir, args.analysis_json, **options)
            # Keep console output bounded; the full result is a structured artifact.
            print(json.dumps({k: v for k, v in result.items() if k != "findings"}, ensure_ascii=False))
            print(f"validation_report={Path(args.analysis_json).with_suffix('.validation.json')}")
            return {"valid": 0, "review_required": 3}.get(result["analysisStatus"], 2)
        elif args.command == "assemble-analysis":
            print(json.dumps(workflow.assemble_analysis(
                Path(args.output_dir) / "diagnostic-analysis", args.part, args.analysis_json,
            ), ensure_ascii=False))
        else:
            print(json.dumps(workflow.standard_run_status(args.output_dir), ensure_ascii=False))
    except DependencyBootstrapError as exc:
        print(f"dependencies: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        if workflow is not None and isinstance(exc, workflow.StandardWorkflowError):
            print(f"{exc.stage}: {exc}", file=sys.stderr)
        else:
            print(f"workflow: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
