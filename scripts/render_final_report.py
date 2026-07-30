#!/usr/bin/env python3
import argparse
import sys

from final_report_lib import FinalReportError, generate_final_report


def build_parser():
    parser = argparse.ArgumentParser(
        description="Render final Qianchuan material HTML report."
    )
    parser.add_argument("--top-materials-json", required=True)
    parser.add_argument("--peak-output-dir", required=True)
    parser.add_argument("--diagnosis-records-dir", required=True)
    parser.add_argument("--analysis-json")
    parser.add_argument("--output-dir", required=True)
    asset_group = parser.add_mutually_exclusive_group()
    asset_group.add_argument(
        "--copy-assets",
        action="store_true",
        help="copy high-light JPEG assets into final-report/assets and reference them",
    )
    asset_group.add_argument(
        "--embed-assets",
        action="store_true",
        help="embed high-light JPEG assets into the generated HTML",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        data_path, html_path, _data = generate_final_report(
            args.top_materials_json,
            args.peak_output_dir,
            args.diagnosis_records_dir,
            args.output_dir,
            analysis_json=args.analysis_json,
            embed_assets=args.embed_assets,
            copy_assets=args.copy_assets,
        )
    except FinalReportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"final_report_data={data_path}")
    print(f"final_report_html={html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
