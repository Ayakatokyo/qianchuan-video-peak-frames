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
    prepare.add_argument("--advertiser-id", help="广告主 ID；省略时从所选 RPA 授权账号解析并核对 API 店铺")
    prepare.add_argument("--shop-id", required=True, help="api_shop 的 API 报表授权店铺 ID")
    prepare.add_argument("--rpa-shop-id", help="rpa_shop 的素材诊断授权账号 ID")
    prepare.add_argument("--output-dir", required=True)

    resume = commands.add_parser("resume", help="Resume an interrupted standard run")
    resume.add_argument("--start-date", required=True)
    resume.add_argument("--end-date", required=True)
    resume.add_argument("--top-n", required=True, type=_positive_int)
    resume.add_argument("--advertiser-id", help="广告主 ID；省略时读取当前运行记录或重新解析授权")
    resume.add_argument("--shop-id", required=True, help="api_shop 的 API 报表授权店铺 ID")
    resume.add_argument("--rpa-shop-id", help="rpa_shop 的素材诊断授权账号 ID")
    resume.add_argument("--output-dir", required=True)

    local = commands.add_parser("prepare-local", parents=[], help="Build missing facts from landed data only")
    local.add_argument("--start-date", required=True)
    local.add_argument("--end-date", required=True)
    local.add_argument("--top-n", required=True, type=_positive_int)
    local.add_argument("--advertiser-id", help="广告主 ID；省略时从匹配的运行摘要读取")
    local.add_argument("--shop-id", required=True, help="api_shop 的 API 报表授权店铺 ID")
    local.add_argument("--rpa-shop-id", help="rpa_shop 的素材诊断授权账号 ID")
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

    task = commands.add_parser("analysis-task", help="Read concise facts, output skeleton and evidence references")
    task.add_argument("--output-dir", required=True)
    task.add_argument("--task")
    task.add_argument("--page", type=int, default=0)
    task.add_argument("--input-sha256")
    task.add_argument("--export-dir", help="Verify once and write all bounded pages to a new directory within diagnostic-analysis")
    task.add_argument("--section", choices=("all", "facts", "contract", "references"), default="all",
                      help="Read facts without repeating the contract and reference table")

    check_part = commands.add_parser("check-analysis-part", help="Check a model-authored chapter or material part before assembly")
    check_part.add_argument("--output-dir", required=True)
    check_part.add_argument("--part", action="append", required=True)

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
    metadata.add_argument("--advertiser-id", help="广告主 ID；省略时从所选 RPA 授权账号解析并核对 API 店铺")
    metadata.add_argument("--shop-id", required=True, help="api_shop 的 API 报表授权店铺 ID")
    metadata.add_argument("--rpa-shop-id", help="用于解析广告主 ID 的 RPA 授权账号 ID；不提交诊断任务")
    metadata.add_argument("--output-dir", required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--output-dir", required=True)
    finalize.add_argument("--analysis-json", required=True)
    finalize.add_argument("--quality-review")
    workbook = finalize.add_mutually_exclusive_group()
    workbook.add_argument("--include-workbook", action="store_true", default=True,
                          help="Generate the highlight-frame XLSX workbook (default)")
    workbook.add_argument("--skip-workbook", dest="include_workbook", action="store_false",
                          help="Explicitly omit Excel export from this delivery")
    finalize.add_argument(
        "--archive",
        action="store_true",
        help="Also package the report directory as final-report.zip",
    )
    finalize.add_argument("--video-cleanup", choices=("dry-run", "delete", "keep"))

    validate_analysis = commands.add_parser("validate-analysis")
    validate_analysis.add_argument("--output-dir", required=True)
    validate_analysis.add_argument("--analysis-json", required=True)
    validate_analysis.add_argument("--quality-review")
    validate_analysis.add_argument("--review-template", help="Write a new review template after structural validation")

    status = commands.add_parser("status")
    status.add_argument("--output-dir", required=True)
    status.add_argument("--wait-seconds", type=int, default=0, choices=range(61), metavar="0..60")
    return parser


def _resolve_advertiser_id(args):
    explicit = str(args.advertiser_id or "").strip()
    if explicit:
        return explicit
    rpa_shop_id = str(args.rpa_shop_id or "").strip()
    if not rpa_shop_id:
        raise ValueError("--rpa-shop-id is required when --advertiser-id is omitted")

    if args.command in {"resume", "prepare-local"}:
        summary_path = Path(args.output_dir) / "run-summary.json"
        if summary_path.is_file():
            saved = json.loads(summary_path.read_text(encoding="utf-8")).get("request") or {}
            expected = {
                "shopId": args.shop_id,
                "rpaShopId": rpa_shop_id,
                "startDate": args.start_date,
                "endDate": args.end_date,
                "topN": args.top_n,
            }
            if any(saved.get(key) != value for key, value in expected.items()):
                raise ValueError("saved run request does not match resume inputs")
            stored = str(saved.get("advertiserId") or "").strip()
            if not (stored.isascii() and stored.isdecimal()):
                raise ValueError("saved run has no numeric advertiser ID")
            return stored
        if args.command == "prepare-local":
            raise ValueError("prepare-local needs --advertiser-id or a matching run-summary.json")

    from rpa_adapter import list_accounts
    from qianchuan_report_client import list_authorized_advertisers

    response = list_accounts()
    rows = response.get("data") if isinstance(response, dict) and response.get("success") is True else None
    if not isinstance(rows, list):
        raise ValueError("RPA authorization query has no usable account list")
    matches = [row for row in rows if isinstance(row, dict)
               and str(row.get("shop_id") or "").strip() == rpa_shop_id]
    if len(matches) != 1:
        raise ValueError("selected RPA authorization ID does not resolve to one account")
    account = str(matches[0].get("account") or "").strip()
    if not (account.isascii() and account.isdecimal()):
        raise ValueError("selected RPA account is not a numeric advertiser ID")
    authorized = [row for row in list_authorized_advertisers() if isinstance(row, dict)
                  and str(row.get("shop_id") or "").strip() == args.shop_id
                  and str(row.get("advertiser_id") or "").strip() == account]
    if len(authorized) != 1:
        raise ValueError("selected RPA account is not uniquely authorized under the API shop")
    return account


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code
    workflow = None
    try:
        if args.command == 'analysis-task':
            from analysis_tasks import task_plan, read_task, export_tasks
            from analysis_task_io import pretty_json
            root = Path(args.output_dir) / 'diagnostic-analysis'
            if args.export_dir:
                if args.page:
                    raise ValueError('export writes all pages; do not combine it with --page')
                result = export_tasks(root, args.export_dir, task_id=args.task,
                                      input_sha256=args.input_sha256, section=args.section)
            else:
                result = (read_task(root, args.task, args.page, args.input_sha256, section=args.section)
                          if args.task else task_plan(root, section=args.section))
            print(pretty_json(result))
            return 0
        if args.command == "check-analysis-part":
            from analysis_handoff import check_analysis_parts
            from analysis_task_io import pretty_json
            root = Path(args.output_dir) / "diagnostic-analysis"
            result = check_analysis_parts(root, args.part)
            print(pretty_json(result))
            return 2 if result.get('errors') else 0
        if args.command == "assemble-analysis":
            from analysis_handoff import assemble_analysis
            from analysis_task_io import pretty_json
            result = assemble_analysis(Path(args.output_dir) / 'diagnostic-analysis', args.part, args.analysis_json)
            print(pretty_json(result))
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
            from analysis_task_io import pretty_json
            root = Path(args.output_dir) / "diagnostic-analysis"
            if args.batch_json and args.rebuild:
                raise ValueError("rebuild and batch checkpoint must be separate commands")
            result = (checkpoint_frame_review(root, read_object(args.batch_json)) if args.batch_json
                      else start_frame_review(root, rebuild=args.rebuild))
            print(pretty_json(result))
            return 0
        if args.command == "cleanup-videos":
            from video_resource_cleanup import cleanup_original_videos
            result = cleanup_original_videos(args.output_dir, mode=args.mode)
            print(json.dumps(result, ensure_ascii=False))
            return 2 if result['status'] == 'partial' else 0
        if args.command == "status":
            from workflow_status import wait_standard_run_status
            print(json.dumps(wait_standard_run_status(args.output_dir, wait_seconds=args.wait_seconds), ensure_ascii=False))
            return 0
        if args.command == "prepare-local":
            ensure_prepare_dependencies(allow_install=False)
        elif args.command == "finalize" and args.include_workbook:
            ensure_prepare_dependencies(include_workbook=True)
        else:
            ensure_prepare_dependencies()
        workflow = _load_workflow()
        if args.command in {"prepare", "prepare-local", "resume"}:
            advertiser_id = _resolve_advertiser_id(args)
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
                    advertiser_id=advertiser_id,
                    shop_id=args.shop_id,
                    rpa_shop_id=args.rpa_shop_id,
                ),
                args.output_dir,
            )
            artifacts = summary["artifacts"]
            print(f"run_summary={Path(args.output_dir) / 'run-summary.json'}")
            if summary.get("status") == "interrupted" and summary.get(
                "activeStage"
            ) == "local_postprocess":
                print(
                    "postprocess_recovery="
                    + json.dumps(summary["postprocessRecovery"], sort_keys=True)
                )
            else:
                print(f"diagnostic_analysis_prompt={Path(args.output_dir) / artifacts['diagnosticPrompt']}")
                print(f"analysis_unit_manifest={Path(args.output_dir) / artifacts['analysisUnitManifest']}")
        elif args.command == "fetch-metadata":
            advertiser_id = _resolve_advertiser_id(args)
            workflow.fetch_report_metadata(
                workflow.WorkflowRequest(
                    start_date=args.start_date,
                    end_date=args.end_date,
                    top_n=None,
                    advertiser_id=advertiser_id,
                    shop_id=args.shop_id,
                    rpa_shop_id=args.rpa_shop_id,
                ),
                args.output_dir,
            )
            print(f"run_summary={Path(args.output_dir) / 'run-summary.json'}")
            print(f"gateway_response={Path(args.output_dir) / 'source-data' / 'gateway-response.json'}")
        elif args.command == "finalize":
            options = {"quality_review": args.quality_review} if args.quality_review else {}
            options["include_workbook"] = args.include_workbook
            options["archive"] = args.archive
            if args.video_cleanup:
                options["video_cleanup"] = args.video_cleanup
            summary = workflow.finalize_standard_run(args.output_dir, args.analysis_json, **options)
            artifacts = summary["artifacts"]
            report_dir = Path(args.output_dir) / "final-report"
            print(f"run_summary={Path(args.output_dir) / 'run-summary.json'}")
            print(f"final_report_dir={report_dir}")
            print(f"final_report_data={report_dir / 'final-report-data.json'}")
            print(f"final_report_html={Path(args.output_dir) / artifacts['report']}")
            if artifacts.get("highlightExport"):
                print(f"highlight_export={Path(args.output_dir) / artifacts['highlightExport']}")
            if artifacts.get("reportArchive"):
                print(f"final_report_zip={Path(args.output_dir) / artifacts['reportArchive']}")
            if summary.get("videoResourceCleanup"):
                print(f"video_cleanup_status={summary['videoResourceCleanup']['status']}")
        elif args.command == "validate-analysis":
            if args.review_template and Path(args.review_template).exists():
                raise ValueError("review template already exists; use --quality-review after editing it")
            options = {"quality_review": args.quality_review} if args.quality_review else {}
            result = workflow.validate_analysis(args.output_dir, args.analysis_json, **options)
            # Keep console output bounded; the full result is a structured artifact.
            print(json.dumps({k: v for k, v in result.items()
                              if k not in {"findings", "reviewDecisions", "contentReviewLimitations", "pendingReviewIds"}},
                             ensure_ascii=False))
            print(f"validation_report={Path(args.analysis_json).with_suffix('.validation.json')}")
            print(f"pending_review_count={len(result.get('pendingReviewIds', []))}")
            if args.review_template and result['analysisStatus'] == 'review_required':
                from analysis_quality import write_review_template
                write_review_template(result, args.review_template)
                print(f"quality_review_template={args.review_template}")
            return {"valid": 0, "review_required": 3}.get(result["analysisStatus"], 2)
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
