#!/usr/bin/env python3
"""CLI adapter for the standard Qianchuan analysis workflow."""

import argparse
import json
from pathlib import Path
import sys

from dependency_bootstrap import DependencyBootstrapError, ensure_prepare_dependencies
from standard_workflow import (
    StandardWorkflowError,
    WorkflowRequest,
    finalize_standard_run,
    prepare_standard_run,
    standard_run_status,
)


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
    prepare.add_argument("--output-dir", required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--output-dir", required=True)
    finalize.add_argument("--analysis-json", required=True)

    status = commands.add_parser("status")
    status.add_argument("--output-dir", required=True)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code
    try:
        if args.command == "prepare":
            ensure_prepare_dependencies()
            summary = prepare_standard_run(
                WorkflowRequest(
                    start_date=args.start_date,
                    end_date=args.end_date,
                    top_n=args.top_n,
                    advertiser_id=args.advertiser_id,
                ),
                args.output_dir,
            )
            artifacts = summary["artifacts"]
            print(f"run_summary={Path(args.output_dir) / 'run-summary.json'}")
            print(f"diagnostic_analysis_data={Path(args.output_dir) / artifacts['diagnosticData']}")
            print(f"diagnostic_analysis_prompt={Path(args.output_dir) / artifacts['diagnosticPrompt']}")
        elif args.command == "finalize":
            summary = finalize_standard_run(args.output_dir, args.analysis_json)
            artifacts = summary["artifacts"]
            report_dir = Path(args.output_dir) / "final-report"
            print(f"run_summary={Path(args.output_dir) / 'run-summary.json'}")
            print(f"final_report_data={report_dir / 'final-report-data.json'}")
            print(f"final_report_html={Path(args.output_dir) / artifacts['report']}")
            print(f"final_report_zip={Path(args.output_dir) / artifacts['reportArchive']}")
        else:
            print(json.dumps(standard_run_status(args.output_dir), ensure_ascii=False))
    except DependencyBootstrapError as exc:
        print(f"dependencies: {exc}", file=sys.stderr)
        return 2
    except StandardWorkflowError as exc:
        print(f"{exc.stage}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
